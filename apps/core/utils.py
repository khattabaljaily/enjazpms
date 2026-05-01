def convert_arabic_numerals(value):
    """Convert Arabic numerals to English in a string."""
    arabic_digits = '٠١٢٣٤٥٦٧٨٩'
    english_digits = '0123456789'
    return ''.join(english_digits[arabic_digits.index(c)] if c in arabic_digits else c for c in str(value))