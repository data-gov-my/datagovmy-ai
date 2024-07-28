import re
import os
import ast
from typing import List, Optional
import requests


# Text Utils
def extract_line_without_hash(markdown_string: str) -> Optional[str]:
    """Extract the first line of a markdown string without the "#" symbol"""

    # Split the multiline string into lines
    lines = markdown_string.split("\n")

    for line in lines:
        # Use regex to find the first occurrence of the "#" symbol at the beginning of the line
        match = re.match(r"^\s*#(.*)", line)
        if match:
            # Extract the line without the "#" character
            return match.group(1).strip()

    # Return None if no line with "#" symbol is found
    return None


def clean_content(text: str) -> str:
    """Remove newlines and other cleaning for better embeddings generation"""
    # TODO: config this remove list
    to_remove = ['<FAQBox title="', '">', "</FAQBox>"]
    text = re.sub(r"#+", "", text)  # remove headings
    text = re.sub(r"\n+", " ", text)  # remove newlines
    for string in to_remove:
        text = text.replace(string, "")
    return text.strip()


def parse_list_string(string: str) -> Optional[str]:
    """Parse DC meta data source string into comma seperated string"""
    try:
        parsed_list = ast.literal_eval(string)  # Evaluate the string as a literal
        return ",".join(parsed_list)
    except (SyntaxError, ValueError):
        return None  # Return None if parsing fails


def parse_desc(sample):
    """Parse DC meta description column into data_type and desc"""
    match = re.match(r"\[(\w+)\]\s*(.+)", sample)
    if match:
        data_type = match.group(1)
        text = match.group(2)
        return data_type, text
    else:
        return None, None


# Others
def send_telegram(message: str) -> None:
    # location = format_header(os.getenv("ENV_LOCATION")) + "\n"
    # message = location + message
    limit = 4096
    if len(message) > limit:
        chunks = [message[i : i + limit] for i in range(0, len(message), limit)]
    else:
        chunks = [message]
    for chunk in chunks:
        params = {
            "chat_id": os.getenv("TELEGRAM_CHAT_ID"),
            "text": chunk,
            "parse_mode": "HTML",
        }
        tf_url = (
            f"https://api.telegram.org/bot{os.getenv('TELEGRAM_TOKEN')}/sendMessage"
        )
        r = requests.get(url=tf_url, data=params)


def read_file_from_repo(repo: str, token: str, file_url: str):
    """Read file from GitHub repo.

    Args:
        repo (str): GitHub repo name
        token (str): GitHub token
        file_url (str): url to file in repo

    Returns:
        str: file content
    """

    headers = {"Authorization": f"token {token}"}

    # Send the request
    response = requests.get(file_url, headers=headers)
    response.raise_for_status()

    return response.text
