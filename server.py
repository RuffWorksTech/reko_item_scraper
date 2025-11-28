import os
from flask import Flask, request, jsonify
import scraper

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def run_scraper():
    print("Incoming request data:", request.data)

    if request.method == "GET":
        url = request.args.get("url")
    else:
        data = request.get_json(silent=True)

        # CASE 1: n8n sends an array: [ { "URL": "..." } ]
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            url = data[0].get("URL")

        # CASE 2: Normal JSON object
        elif isinstance(data, dict):
            url = data.get("URL")

        else:
            url = None

    if not url:
        return jsonify({
            "error": "No URL provided",
            "received_body": request.get_json(silent=True),
            "raw": request.data.decode(errors='ignore')
        }), 400

    result = scraper.scrape_site(url)
    return jsonify({"status": "ok", "result": result})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
