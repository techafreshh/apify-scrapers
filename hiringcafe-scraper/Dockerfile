FROM apify/actor-python-playwright-chrome:3.11

# Copy requirements and install dependencies
# Playwright + Chrome are preinstalled in the base image — do not reinstall them here.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . ./

# Run the actor
CMD ["python3", "-m", "src.main"]
