from datetime import datetime

# DD-MM-YYYY format
def get_current_date():
    return datetime.now().strftime("%Y-%m-%d")
    # Other format - print(date.strftime("%A: %d/%B"))