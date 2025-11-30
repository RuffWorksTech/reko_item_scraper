import os
from flask import Flask, request, jsonify
import scraper

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def run():
    # ---- POST JSON BODY ----
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        url = data.get("URL") or data.get("url")
        
        # Optional: API base URL and agent token for progress updates
        api_base_url = data.get("apiBaseUrl")
        agent_token = data.get("agentToken")

        if not url:
            return jsonify({
                "error": "Missing URL in JSON body"
            }), 400

        result = scraper.scrape_site(url, api_base_url=api_base_url, agent_token=agent_token)
        return jsonify({"status": "ok", "result": result}), 200

    # ---- GET QUERY PARAM ----
    url = request.args.get("url")
    if not url:
        return jsonify({
            "error": "Please pass ?url=https://sitename.com OR send POST JSON {\"URL\": \"...\"}"
        }), 400

    result = scraper.scrape_site(url)
    return jsonify({"status": "ok", "result": result}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
