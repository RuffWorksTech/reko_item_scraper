# Makefile for Reko Item Scraper
# Provides convenient commands for local development

.PHONY: help start stop restart logs build test clean status

# Default target - show help
help:
	@echo "Reko Item Scraper - Make Commands"
	@echo "=================================="
	@echo ""
	@echo "🚀 Quick Start (Development):"
	@echo "  yarn start      - Start directly (fastest, recommended for dev)"
	@echo ""
	@echo "🐳 Docker commands:"
	@echo "  make start      - Start the scraper service with Docker"
	@echo "  make stop       - Stop the scraper service"
	@echo "  make restart    - Restart the scraper service"
	@echo "  make logs       - View scraper logs (follow mode)"
	@echo "  make build      - Rebuild the Docker image"
	@echo "  make test       - Test if the service is running"
	@echo "  make status     - Show container status"
	@echo "  make clean      - Stop and remove containers/images"
	@echo ""
	@echo "Examples:"
	@echo "  yarn start                - Start for active development"
	@echo "  make start && make logs   - Start with Docker and view logs"
	@echo "  make restart              - Restart after code changes"
	@echo ""

# Start the scraper service in detached mode
start:
	@echo "🚀 Starting Reko Item Scraper..."
	docker-compose up -d --build
	@echo "✅ Scraper is running at http://localhost:8080"
	@echo "📝 View logs with: make logs"

# Stop the scraper service
stop:
	@echo "🛑 Stopping Reko Item Scraper..."
	docker-compose down
	@echo "✅ Scraper stopped"

# Restart the service (useful after code changes)
restart:
	@echo "🔄 Restarting Reko Item Scraper..."
	docker-compose down
	docker-compose up -d --build
	@echo "✅ Scraper restarted at http://localhost:8080"
	@echo "📝 View logs with: make logs"

# View logs in follow mode (Ctrl+C to exit)
logs:
	@echo "📋 Showing scraper logs (Ctrl+C to exit)..."
	docker-compose logs -f scraper

# Rebuild the Docker image from scratch
build:
	@echo "🔨 Building Docker image..."
	docker-compose build --no-cache
	@echo "✅ Build complete"

# Test if the service is running and responding
test:
	@echo "🧪 Testing scraper service..."
	@curl -s -o /dev/null -w "Status: %{http_code}\n" "http://localhost:8080/?url=https://example.com" || echo "❌ Service not responding. Make sure it's running with: make start"
	@echo "✅ Test complete"

# Show container status
status:
	@echo "📊 Container Status:"
	@docker-compose ps

# Clean up - remove containers and images
clean:
	@echo "🧹 Cleaning up containers and images..."
	docker-compose down --rmi all --volumes
	@echo "✅ Cleanup complete"

# Start the service and show logs immediately
start-logs: start
	@echo "📋 Showing logs (Ctrl+C to stop viewing, service continues running)..."
	@sleep 2
	docker-compose logs -f scraper

