"""
Convert PDF files in the files folder to text files.
Extracts text content only, ignoring images.
"""
import os
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:
    print("pypdf not installed. Installing...")
    os.system("pip install pypdf")
    from pypdf import PdfReader


def convert_pdf_to_text(pdf_path, output_path):
    """Extract text from PDF and save to text file."""
    try:
        reader = PdfReader(pdf_path)
        text_content = []

        print(f"Processing: {pdf_path.name}")
        print(f"  Pages: {len(reader.pages)}")

        for page_num, page in enumerate(reader.pages, 1):
            text = page.extract_text()
            if text.strip():
                text_content.append(f"--- Page {page_num} ---\n{text}\n")

        # Write to text file
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(text_content))

        print(f"  [OK] Saved to: {output_path.name}\n")
        return True

    except Exception as e:
        print(f"  [ERROR] {e}\n")
        return False


def main():
    # Setup paths
    files_dir = Path(__file__).parent / "files"

    if not files_dir.exists():
        print(f"Error: {files_dir} not found")
        return

    # Find all PDF files
    pdf_files = list(files_dir.glob("*.pdf"))

    if not pdf_files:
        print("No PDF files found in files folder")
        return

    print(f"Found {len(pdf_files)} PDF files\n")
    print("=" * 60)

    # Convert each PDF
    success_count = 0
    for pdf_path in sorted(pdf_files):
        output_path = pdf_path.with_suffix('.txt')
        if convert_pdf_to_text(pdf_path, output_path):
            success_count += 1

    print("=" * 60)
    print(f"Conversion complete: {success_count}/{len(pdf_files)} files converted")


if __name__ == "__main__":
    main()
