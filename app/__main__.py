import logging

import uvicorn

from dotenv import load_dotenv

from app.server import create_app
from app.settings import Settings


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == '__main__':
    main()
