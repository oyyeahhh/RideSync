web: python startup.py && gunicorn portal:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 -k gthread --timeout 120
