import re
import ast
from typing import List, Optional
import requests

from config import *

# Text Utils
def extract_line_without_hash(markdown_string: str) -> Optional[str]:
    # Split the multiline string into lines
    lines = markdown_string.split('\n')

    for line in lines:
        # Use regex to find the first occurrence of the "#" symbol at the beginning of the line
        match = re.match(r'^\s*#(.*)', line)
        if match:
            # Extract the line without the "#" character
            return match.group(1).strip()

    # Return None if no line with "#" symbol is found
    return None


def clean_content(text: str) -> str:
    """Remove newlines and other cleaning for better embeddings generation"""
    # TODO: config this remove list
    to_remove = [
        '<FAQBox title="',
        '">',
        '</FAQBox>'
        ]
    text = re.sub(r'#+', '', text) # remove headings
    text = re.sub(r'\n+', ' ', text) # remove newlines
    for string in to_remove:
        text = text.replace(string, '')
    return text.strip()


def parse_list_string(string: str) -> Optional[str]:
    """Parse data source string into comma seperated string"""
    try:
        parsed_list = ast.literal_eval(string)  # Evaluate the string as a literal
        return ','.join(parsed_list)
    except (SyntaxError, ValueError):
        return None  # Return None if parsing fails
    
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
            "chat_id": settings.TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
        }
        tf_url = (
            f'https://api.telegram.org/bot{settings.TELEGRAM_TOKEN}/sendMessage'
        )
        r = requests.get(url=tf_url, data=params)