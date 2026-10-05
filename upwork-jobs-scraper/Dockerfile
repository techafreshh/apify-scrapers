FROM apify/actor-python-playwright:3.11

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Ensure playwright browser binaries are installed
RUN playwright install chromium

COPY . ./

CMD ["python3", "-m", "src.main"]
