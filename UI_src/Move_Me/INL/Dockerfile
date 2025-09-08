# Use Python 3.9 slim image for Windows compatibility
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV STREAMLIT_SERVER_PORT=8501
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# Add certificate to Docker
COPY CAINLROOT.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
COPY lib/ ./lib/
COPY pages/ ./pages/
COPY __init__.py .
COPY .streamlit/ ./.streamlit/

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --cert $SSL_CERT_FILE --retries 10 --timeout 60 --no-cache-dir -r requirements.txt


# Copy application code
COPY Main.py .

# Create non-root user for security
RUN adduser --disabled-password --gecos '' streamlituser
USER streamlituser

# Expose port
EXPOSE 8501

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl --fail http://localhost:8501/_stcore/health || exit 1

# Run Streamlit
ENTRYPOINT ["streamlit", "run", "/app/Main.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]