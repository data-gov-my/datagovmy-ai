import weaviate
import argparse


def restore_data(backup_id: str, url: str, class_name: str = None) -> None:
    client = weaviate.Client(url)

    result = client.backup.restore(
        backup_id=backup_id, backend="filesystem", include_classes=class_name
    )


def backup_data(backup_id: str, url: str, class_name: str = None) -> None:
    client = weaviate.Client(url)

    result = client.backup.create(
        backup_id=backup_id, backend="filesystem", include_classes=class_name
    )


if __name__ == "__main__":
    # example usage
    # python backup_script.py your_backup_id_here --url <url here>"
    parser = argparse.ArgumentParser(description="Setup weaviate client")
    parser.add_argument(
        "backup_id", type=str, help="Specify the backup ID as a string."
    )
    parser.add_argument(
        "--url", type=str, help="Specify weaviate URL for the backup (optional)."
    )
    parser.add_argument(
        "--class_name",
        type=str,
        default=None,
        help="Specify the class name (optional).",
    )

    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument(
        "--backup", action="store_true", help="Toggle backup action."
    )
    action_group.add_argument(
        "--restore", action="store_true", help="Toggle restore action."
    )
    args = parser.parse_args()

    if args.backup:
        print("Performing backup action for backup ID:", args.backup_id)
        backup_data(args.backup_id, args.url, args.class_name)
    elif args.restore:
        print("Performing restore action for backup ID:", args.backup_id)
        restore_data(args.backup_id, args.url, args.class_name)
