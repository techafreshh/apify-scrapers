FROM apify/actor-python-playwright:3.11

# Copy requirements and install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Ensure playwright browser binaries are installed
RUN playwright install chromium

# Copy source code
COPY . ./

# Run the actor
CMD ["python3", "-m", "src.main"]
