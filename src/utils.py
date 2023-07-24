import re


def extract_line_without_hash(markdown_string):
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

def clean_content(text, to_remove):
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


def parse_list_string(string):
    """Parse data source string into comma seperated string"""
    try:
        parsed_list = ast.literal_eval(string)  # Evaluate the string as a literal
        return ','.join(parsed_list)
    except (SyntaxError, ValueError):
        return None  # Return None if parsing fails