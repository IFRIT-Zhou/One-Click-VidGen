"""Run once with Cloud API's production environment before exposing H3 routes."""
from app.cloud_video_api import create_schema
from app.db import engine

if __name__ == '__main__':
    create_schema(engine)
    print('H3 additive schema created')
