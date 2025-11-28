import os
from flask import Flask, request, jsonify
import scraper

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def run_scraper():

    if request.method == "GET":
        url = request.args.get("url")

    else:
        data = request.get_json(silent=True)

        # n8n style: [ { "URL": "..." } ]
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            url = data[0].get("URL")

        # normal JSON: { "URL": "..." }
        elif isinstance(data, dict):
            url = data.get("URL")

        else:
            url = None

    if not url:
        return jsonify({
            "error": "No URL provided. Valid formats:\n"
                     "- GET /?url=...\n"
                     "- POST { \"URL\": \"...\" }\n"
                     "- POST [ { \"URL\": \"...\" } ]"
        }), 400

    result = scraper.scrape_site(url)
    return jsonify({"status": "ok", "result": result})

