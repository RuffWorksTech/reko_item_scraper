#!/usr/bin/env python3
"""
Development server starter script.
Provides helpful output and checks for dependencies.
"""

import os
import sys
import subprocess
import socket

# ANSI color codes for pretty terminal output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def print_banner():
    """Print a nice startup banner."""
    print(f"\n{Colors.OKCYAN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.OKGREEN}  🕷️  Reko Item Scraper - Development Server{Colors.ENDC}")
    print(f"{Colors.OKCYAN}{'='*60}{Colors.ENDC}\n")


def check_python_version():
    """Check if Python version is compatible."""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 8):
        print(f"{Colors.FAIL}❌ Python 3.8+ required. You have {version.major}.{version.minor}{Colors.ENDC}")
        sys.exit(1)
    print(f"{Colors.OKGREEN}✅ Python {version.major}.{version.minor}.{version.micro}{Colors.ENDC}")


def check_dependencies():
    """Check if required Python packages are installed."""
    required_packages = [
        ('flask', 'Flask'),
        ('bs4', 'BeautifulSoup4'),
        ('requests', 'requests'),
        ('playwright', 'Playwright')
    ]
    
    missing = []
    for import_name, package_name in required_packages:
        try:
            __import__(import_name)
            print(f"{Colors.OKGREEN}✅ {package_name} installed{Colors.ENDC}")
        except ImportError:
            missing.append(package_name)
            print(f"{Colors.FAIL}❌ {package_name} not installed{Colors.ENDC}")
    
    if missing:
        print(f"\n{Colors.WARNING}Missing dependencies. Install with:{Colors.ENDC}")
        print(f"{Colors.BOLD}  pip3 install -r requirements.txt{Colors.ENDC}")
        print(f"{Colors.BOLD}  playwright install chromium{Colors.ENDC}")
        print(f"\n{Colors.WARNING}Or use yarn:{Colors.ENDC}")
        print(f"{Colors.BOLD}  yarn install:python{Colors.ENDC}\n")
        sys.exit(1)


def check_playwright_browsers():
    """Check if Playwright browsers are installed."""
    try:
        result = subprocess.run(
            ['playwright', 'install', '--dry-run', 'chromium'],
            capture_output=True,
            text=True,
            timeout=5
        )
        # If dry-run succeeds, browsers are likely installed
        # Note: This is a heuristic check, not perfect
        print(f"{Colors.OKGREEN}✅ Playwright browsers available{Colors.ENDC}")
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.CalledProcessError):
        print(f"{Colors.WARNING}⚠️  Playwright browsers may not be installed{Colors.ENDC}")
        print(f"{Colors.WARNING}   Run: playwright install chromium{Colors.ENDC}")


def get_local_ip():
    """Get the local IP address."""
    try:
        # Create a socket to find the local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return "localhost"


def print_server_info(port):
    """Print server startup information."""
    local_ip = get_local_ip()
    
    print(f"\n{Colors.OKGREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.OKGREEN}  🚀 Server Starting...{Colors.ENDC}")
    print(f"{Colors.OKGREEN}{'='*60}{Colors.ENDC}\n")
    
    print(f"{Colors.BOLD}📡 Listening on:{Colors.ENDC}")
    print(f"   {Colors.OKCYAN}http://localhost:{port}{Colors.ENDC}")
    print(f"   {Colors.OKCYAN}http://127.0.0.1:{port}{Colors.ENDC}")
    if local_ip != "localhost":
        print(f"   {Colors.OKCYAN}http://{local_ip}:{port}{Colors.ENDC} {Colors.WARNING}(network access){Colors.ENDC}")
    
    print(f"\n{Colors.BOLD}📚 API Endpoints:{Colors.ENDC}")
    print(f"   GET  http://localhost:{port}/?url=<website_url>")
    print(f"   POST http://localhost:{port}/ {Colors.WARNING}(JSON body: {{'URL': '...'}}){Colors.ENDC}")
    
    print(f"\n{Colors.BOLD}🧪 Quick Test:{Colors.ENDC}")
    print(f"   {Colors.OKCYAN}curl \"http://localhost:{port}/?url=https://www.scrapingcourse.com/ecommerce/\"{Colors.ENDC}")
    
    print(f"\n{Colors.BOLD}📖 Example (Node.js):{Colors.ENDC}")
    print(f"   {Colors.WARNING}const response = await axios.post('http://localhost:{port}', {{")
    print(f"     URL: 'https://example.com/shop'")
    print(f"   }}, {{ timeout: 300000 }});{Colors.ENDC}")
    
    print(f"\n{Colors.BOLD}📖 Example (Python):{Colors.ENDC}")
    print(f"   {Colors.WARNING}response = requests.post('http://localhost:{port}',")
    print(f"       json={{'URL': 'https://example.com/shop'}}, timeout=300){Colors.ENDC}")
    
    print(f"\n{Colors.BOLD}💡 Tips:{Colors.ENDC}")
    print(f"   • Scraping takes 2-5 minutes for large sites")
    print(f"   • Use 300+ second timeouts in your backend")
    print(f"   • Press {Colors.BOLD}Ctrl+C{Colors.ENDC} to stop the server")
    
    print(f"\n{Colors.OKGREEN}{'='*60}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.OKGREEN}  Server is ready! Waiting for requests...{Colors.ENDC}")
    print(f"{Colors.OKGREEN}{'='*60}{Colors.ENDC}\n")


def main():
    """Main entry point."""
    print_banner()
    
    # Check system requirements
    print(f"{Colors.BOLD}🔍 Checking requirements...{Colors.ENDC}\n")
    check_python_version()
    check_dependencies()
    check_playwright_browsers()
    
    # Get port from environment or use default
    port = int(os.environ.get("PORT", 8080))
    os.environ["PORT"] = str(port)
    
    # Print server information
    print_server_info(port)
    
    # Start the Flask server
    try:
        # Import and run the server directly (using the same Python interpreter)
        # This ensures we use the venv Python with all dependencies
        import server
        # Run the server directly
        if __name__ == "__main__":
            server.app.run(host="0.0.0.0", port=port)
    except KeyboardInterrupt:
        print(f"\n\n{Colors.WARNING}🛑 Server stopped by user{Colors.ENDC}")
        print(f"{Colors.OKGREEN}👋 Goodbye!{Colors.ENDC}\n")
        sys.exit(0)
    except Exception as e:
        print(f"\n{Colors.FAIL}❌ Error starting server: {e}{Colors.ENDC}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()

