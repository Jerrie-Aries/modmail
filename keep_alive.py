from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Optional

from core.logging_ext import getLogger

logger = getLogger(__name__)

HOST = "0.0.0.0"
PORT = 8000


class HTTPRequestHandler(BaseHTTPRequestHandler):
    """
    A simple HTTP request handler to monitor the program on `Repl.it` with UptimeRobot.

    This class inherits from `http.server.BaseHTTPRequestHandler`. Instead of inheriting
    the `http.server.SimpleHTTPRequestHandler` class, we inherit from the base class and manually
    construct it.
    """

    __html_content = (
        '<div align="center"><img src="https://i.imgur.com/o558Qnq.png" align="center">'
    )

    def do_GET(self) -> None:
        """Serve a GET request."""
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(bytes(self.__html_content, "utf8"))

    def do_HEAD(self) -> None:
        """Serve a HEAD request."""
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(bytes(self.__html_content, "utf8"))


server: Optional[HTTPServer] = None  # implemented in `run()`


def run() -> None:
    """
    Runs the HTTP server.
    """
    global server
    with HTTPServer((HOST, PORT), HTTPRequestHandler) as server:
        logger.info("Web server started.")
        server.serve_forever()


def shutdown() -> None:
    """
    Stops the `serve_forever` loop.
    """
    if server is not None:
        logger.warning(" - Shutting down web server. - ")
        server.shutdown()


def keep_alive() -> HTTPServer:
    """
    Main function to run the HTTP server inside a new thread.

    Once this is executed, the `server` variable we defined above will be replaced with
    the actual :class:`HTTPServer` instance.

    Returns
    -------
    HTTPServer
        The instance of HTTPServer created, defined in `server` variable above and
        replaced by global in `run` function.
    """
    thread = Thread(target=run)
    thread.start()
    return server


if __name__ == "__main__":
    logger.info("Starting web server without bot.")
    try:
        run()
    except KeyboardInterrupt:
        shutdown()
