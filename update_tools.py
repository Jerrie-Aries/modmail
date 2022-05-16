import asyncio
import os
import shutil
import traceback
import zipfile
from io import BytesIO
from pathlib import Path, PurePath
from typing import Optional

from aiohttp import ClientSession
from dotenv import load_dotenv

load_dotenv()

_base_api_ = "https://api.github.com"
_base_url_ = "https://github.com"
_username_ = os.getenv("GITHUB_USERNAME")
_repository_ = os.getenv("GITHUB_REPO")
_branch_ = "master"


# working directories and paths
current_path = Path.cwd().resolve()
temp_path = current_path / "temp"
cache_zip = temp_path / f"{_repository_}.zip"

# ignored directories and paths
_ignored_files = [
    "update_tools.py",  # need to skip these as well to avoid crash
    "update.sh",
]
ignored_files = [current_path / f for f in _ignored_files]
ignored_dirs = (".github",)
old_cache_dirs = ("__pycache__",)


def get_url() -> str:
    return f"{_base_url_}/{_username_}/{_repository_}/archive/{_branch_}.zip"


class ProjectManager:
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop = None,
        use_cache: bool = False,
        save_temp: bool = False,
    ):
        self.loop: asyncio.AbstractEventLoop = loop or asyncio.get_event_loop()
        self.use_cache: bool = use_cache
        self.save_temp: bool = save_temp
        self.gh_token: Optional[str] = os.getenv("GITHUB_TOKEN")

        if self.gh_token is not None:
            self.headers = {"Authorization": f"token {self.gh_token}"}
        else:
            self.headers = {}

        self.session: Optional[ClientSession] = None

    async def run(self):
        try:
            await self.choose_branch()
            await self.clone()
        except Exception as exc:
            print("Unexpected exception.")
            print(f"{type(exc).__name__}: {str(exc)}")
            traceback.print_exc()
        finally:
            if self.session:
                await self.session.close()

    async def choose_branch(self) -> None:
        if self.session is None:
            self.session = ClientSession(loop=self.loop)

        print(f"Fetching list of branches...")
        url = f"{_base_api_}/repos/{_username_}/{_repository_}/branches"
        async with self.session.get(url, headers=self.headers) as resp:
            if resp.status == 404:
                raise TypeError(f"{url} not found.")

            raw = await resp.json()

        branch_mapping = {}
        for i, branch in enumerate(raw, start=1):
            branch_mapping[str(i)] = branch["name"]

        print("List of branches:")
        for k, v in branch_mapping.items():
            print(f"{k}. {v}")

        print('Select the branch number you want to download. To cancel, press "q":')
        selected = None
        while range(5):
            user_input = input()
            if user_input in branch_mapping.keys():
                selected = user_input
                break
            elif user_input.lower() == "q":
                break
            else:
                print("Wrong input, please try again.")
                continue

        if selected is None:
            raise ValueError("No branch selected. Cancelling.")

        print(f'Branch "{selected}. {branch_mapping[selected]}" is selected.')
        global _branch_
        _branch_ = branch_mapping[selected]

    async def clone(self) -> None:
        if self.use_cache and cache_zip.exists():
            raw = cache_zip.read_bytes()
        else:
            raw = await self.download_zip()

        repo_io = BytesIO(raw)

        with zipfile.ZipFile(repo_io) as zipf:
            print(f"Extracting the downloaded file...")
            for info in zipf.infolist():
                # convert to PurePath object first to have access to `.parts` method that
                # returns a tuple of strings joined off of this path
                # the slice `[1:]` is used to remove the parent folder name which here would be
                # the repository name
                parts = PurePath(info.filename).parts[1:]

                # loop and check if one of the ignored dirs in the parts
                # this way, we can also ignore the dir that's not in the root directory
                ignore = False
                for ig in ignored_dirs:
                    if ig in parts:
                        ignore = True
                        break
                if ignore:
                    continue

                # create a Path object by joining the parts
                file_path = Path(*parts)

                file_path = current_path / file_path
                # file path is used here to make sure we don't mess up with files in
                # other paths
                if file_path in ignored_files:
                    continue

                if info.is_dir():
                    file_path.mkdir(parents=True, exist_ok=True)
                else:
                    print(f"Extracting {info.filename}")
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    with zipf.open(info) as src, file_path.open("wb") as dst:
                        shutil.copyfileobj(src, dst)

        print(f"Successfully downloaded and extracted repository.")
        repo_io.close()
        await self.clean_up()
        print(f"Project is ready.")

    async def download_zip(self, url: str = None) -> bytes:
        """
        Downloads the zip file from the repository. If the repository URL is invalid, or non-bytes
        object is received, TypeError will be raised.
        """
        if self.session is None:
            self.session = ClientSession(loop=self.loop)

        if url is None:
            url = get_url()

        print(f"Downloading {url}")
        print("This may take a moment...")
        async with self.session.get(url, headers=self.headers) as resp:
            if resp.status == 404:
                raise TypeError(f"Repository {url} not found.")

            raw = await resp.read()

            try:
                raw = await resp.text()
            except UnicodeDecodeError:
                # this means `raw` is a bytes object and cannot be converted to text,
                # so we're good to go
                pass
            else:
                msg = "Invalid download received, non-bytes object."
                raise TypeError(msg)

        if self.save_temp:
            self.save_zip(raw)

        return raw

    @staticmethod
    def save_zip(raw: bytes) -> None:
        if not temp_path.exists():
            print(f'"{temp_path}" does not exist. Creating a new one.')
            temp_path.mkdir(parents=True)

        with cache_zip.open("wb") as f:
            print(f'Saving the zip file into "{temp_path}".')
            f.write(raw)

    async def clean_up(self) -> None:
        print("Cleaning up old cached files...")
        self.scandir_and_remove(current_path)

    def scandir_and_remove(self, path: Path) -> None:
        """
        Removes old cache directories. This will recursively scan and remove any directory that
        matches any name specified in `old_cache_dirs` variable.
        """
        for entry in path.iterdir():
            if entry.is_dir():
                if entry.name in old_cache_dirs:
                    print(f"Deleting {entry}")
                    shutil.rmtree(
                        entry,
                        onerror=lambda *args: print(
                            f"Failed to remove dir {entry}: {str(args[2])}"
                        ),
                    )
                else:
                    self.scandir_and_remove(entry)


async def main():
    loop = asyncio.get_event_loop()
    manager = ProjectManager(loop)
    try:
        await manager.run()
    finally:
        await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(main())
