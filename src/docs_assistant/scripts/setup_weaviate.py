import weaviate
import argparse

def restore_data(backup_id: str,
                url: str) -> None:

    client = weaviate.Client(url)

    result = client.backup.restore(
        backup_id=backup_id,
        backend='filesystem',
)

if __name__ == "__main__":
    # example usage
    # python backup_script.py your_backup_id_here --url <url here>"
    parser = argparse.ArgumentParser(description="Setup weaviate client")
    parser.add_argument("backup_id", type=str, help="Specify the backup ID as a string.")
    parser.add_argument("--url", type=str, help="Specify weaviate URL for the backup (optional).")
    args = parser.parse_args()

    restore_data(args.backup_id, args.url)