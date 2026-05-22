from __future__ import annotations

import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.db.store import Store
from backend.pipeline.crawler import SiteCrawler

PAGE_COUNT = 500
HOST_COUNT = 5
MAX_SECONDS = 30.0


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


def main() -> int:
    servers: list[ThreadingHTTPServer] = []
    threads: list[threading.Thread] = []
    try:
        urls = []
        for host_index in range(HOST_COUNT):
            server = QuietThreadingHTTPServer(("127.0.0.1", 0), _handler_for_host(host_index))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            servers.append(server)
            threads.append(thread)
            port = server.server_address[1]
            for page_index in range(PAGE_COUNT // HOST_COUNT):
                urls.append(f"http://127.0.0.1:{port}/page/{page_index}")

        with TemporaryDirectory() as tmp_dir:
            store = Store(f"sqlite:///{Path(tmp_dir) / 'bench.db'}")
            store.init_db()
            crawler = SiteCrawler(store=store, pages_per_alum=len(urls))
            seed = [{"name": "Bench Person", "class_year": "T'26", "urls": urls}]
            started = time.perf_counter()
            asyncio.run(crawler.run(seed, lambda event: None))
            elapsed = time.perf_counter() - started
            fetched = len(store.list_raw_pages())

        pages_per_second = fetched / elapsed if elapsed else 0.0
        print(f"pages/sec: {pages_per_second:.2f}")
        print(f"total wall time: {elapsed:.2f}s")
        print(f"pages fetched: {fetched}")
        if elapsed > MAX_SECONDS or fetched != PAGE_COUNT:
            return 1
        return 0
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=1)


def _handler_for_host(host_index: int) -> type[BaseHTTPRequestHandler]:
    class BenchHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/robots.txt":
                self.send_response(200)
                self.send_header("content-type", "text/plain")
                self.end_headers()
                self.wfile.write(b"User-agent: *\n")
                return
            if not self.path.startswith("/page/"):
                self.send_response(404)
                self.end_headers()
                return
            page_id = self.path.rsplit("/", 1)[-1]
            body = (
                "<html><head><title>Bench Page</title></head><body>"
                f"<main>Bench host {host_index} page {page_id}. "
                "The alumnus is a Senior Manager at Acme Corp. "
                "Previously worked at Beta Inc and Gamma LLC. Dartmouth Tuck MBA."
                "</main></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    return BenchHandler


if __name__ == "__main__":
    raise SystemExit(main())
