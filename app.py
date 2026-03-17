"""주식 분석 대시보드 - Flask 백엔드"""

import json
import os
import anthropic
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analyze", methods=["POST"])
def analyze():
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다."}), 500

    data = request.get_json()
    if not data:
        return jsonify({"error": "요청 데이터가 없습니다."}), 400

    prompt = data.get("prompt", "")
    if not prompt:
        return jsonify({"error": "프롬프트가 비어 있습니다."}), 400

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=4000,
            thinking={"type": "adaptive"},
            tools=[
                {"type": "web_search_20260209", "name": "web_search"},
            ],
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            response = stream.get_final_message()

        # Extract text content blocks
        text_content = "".join(
            block.text for block in response.content if block.type == "text"
        )

        # Strip markdown code fences if present
        clean = text_content.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[-1]
            if clean.endswith("```"):
                clean = clean[: clean.rfind("```")]
        clean = clean.strip()

        parsed = json.loads(clean)
        return jsonify({"success": True, "data": parsed})

    except json.JSONDecodeError as e:
        return jsonify({"error": f"AI 응답 파싱 실패: {str(e)}", "raw": text_content}), 500
    except anthropic.AuthenticationError:
        return jsonify({"error": "API 키가 유효하지 않습니다."}), 401
    except anthropic.RateLimitError:
        return jsonify({"error": "API 호출 한도에 도달했습니다. 잠시 후 다시 시도해주세요."}), 429
    except anthropic.APIStatusError as e:
        return jsonify({"error": f"API 오류: {e.message}"}), e.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
