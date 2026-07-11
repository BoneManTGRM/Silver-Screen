# Utility functions for Silver-Screen personal movie maker
import os
from datetime import datetime
def create_project_folder(name):
    folder = f'projects/{name}_{datetime.now().strftime("%Y%m%d")}'
    os.makedirs(folder, exist_ok=True)
    return folder
def save_character(name, description, ref_image_path=None):
    print(f'Character {name} saved for consistency.') # Extend with JSON or DB
    return True
print('Utilities loaded for personal use.')