EPL Inventory Checker v0.5.3
============================

Automatic Tesseract detection
-----------------------------

The program now automatically searches for Tesseract OCR in:

- The Windows PATH
- C:\Program Files\Tesseract-OCR\tesseract.exe
- C:\Program Files (x86)\Tesseract-OCR\tesseract.exe
- The user's Local AppData Programs folder
- A Tesseract-OCR folder beside the application

Users no longer need to manually add Tesseract to the Windows PATH.
They only need to install Tesseract using its normal Windows installer.

Install this update
-------------------

Replace main.py in the project folder, then run:

    python main.py
