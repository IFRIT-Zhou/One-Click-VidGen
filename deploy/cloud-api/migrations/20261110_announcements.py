"""Create the announcement table. Run with deploy/cloud-api on PYTHONPATH."""
from app.announcements import create_schema
from app.db import engine


if __name__ == "__main__":
    create_schema(engine)
    print("Announcement schema is ready; no announcements were published.")
