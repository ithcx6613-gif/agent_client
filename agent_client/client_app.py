from flask import Flask, request, session, jsonify, render_template, redirect
from auth_helper import AuthHelper
from agent_client import AgentClient
from flask_cors import CORS
import os
import secrets

# ---------------------------------------------------------------------------
# App bootstrap
# ---------------------------------------------------------------------------
app = Flask(__name__)
CORS(app, supports_credentials=True)

app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_REFRESH_EACH_REQUEST"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = 3600  # 1 hour

auth_helper = AuthHelper()

SESSION_TOKEN_KEY = "azure_access_token"
SESSION_TOKEN_EXP = "azure_token_exp"
SESSION_USER_NAME = "azure_user_name"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _user_info() -> dict | None:
    token = session.get(SESSION_TOKEN_KEY)
    if not token:
        return None
    try:
        import base64, json
        _, payload_b64, _ = token.split(".")
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return {
            "name": payload.get("name", ""),
            "email": payload.get("email", ""),
            "exp": payload.get("exp", 0),
            "aud": payload.get("aud", ""),
            "tid": payload.get("tid", ""),
        }
    except Exception:
        return {"name": "", "email": ""}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/auth/login")
def auth_login():
    """Redirect the browser directly to Microsoft's OAuth authorization page."""
    try:
        auth_uri = auth_helper.interactive_login()
        print(f"[Auth] Redirecting user to {auth_uri[:80]}...")
        return redirect(auth_uri)
    except Exception as e:
        print(f"[Auth] Failed to initiate login: {e}")
        return redirect(f"/?error={str(e)}")


@app.route("/auth/status")
def auth_status():
    info = _user_info()
    if not info:
        return jsonify({"code": 200, "data": {"logged_in": False}})
    return jsonify({"code": 200, "data": {"logged_in": True, "user": info}})


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    session.pop(SESSION_TOKEN_KEY, None)
    session.pop(SESSION_TOKEN_EXP, None)
    session.pop(SESSION_USER_NAME, None)
    session.pop("auth_code_flow", None)
    return jsonify({"code": 200, "msg": "已退出登录"})


@app.route("/callback")
def callback():
    if "error" in request.args:
        print(f"[Callback] Error: {request.args['error']}")
        return redirect(f"/?error={request.args['error']}")

    auth_code = request.args.get("code")
    if not auth_code:
        return redirect("/?error=未获取到授权码")

    auth_code_flow = session.get("auth_code_flow")
    if not auth_code_flow:
        return redirect("/?error=授权流程已过期，请重新点击授权按钮")

    if request.args.get("state") != auth_code_flow.get("state"):
        print("[Callback] State mismatch")
        return redirect("/?error=State验证失败，请重新授权")

    try:
        result = auth_helper.app.acquire_token_by_auth_code_flow(
            auth_code_flow=auth_code_flow,
            auth_response=request.args,
        )
    except ValueError as e:
        print(f"[Callback] Token exchange failed: {e}")
        return redirect(f"/?error=授权流程异常：{str(e)}")

    if "access_token" not in result:
        print(f"[Callback] Token missing: {result.get('error_description', '')}")
        return redirect(f"/?error={result.get('error', '令牌兑换失败')}")

    access_token = result["access_token"]
    expires_in = result.get("expires_in", 3600)

    session[SESSION_TOKEN_KEY] = access_token
    session[SESSION_TOKEN_EXP] = expires_in
    session.pop("auth_code_flow", None)

    print(f"[Callback] Token acquired, expires in {expires_in}s")
    return redirect("/")


# ---------------------------------------------------------------------------
# Agent diagnostic endpoint
# ---------------------------------------------------------------------------
@app.route("/api/agent/check", methods=["GET"])
def check_agent():
    """Check if the configured agent exists and return its info."""
    access_token = session.get(SESSION_TOKEN_KEY)
    if not access_token:
        return jsonify({"code": 401, "msg": "未登录", "data": None}), 401

    try:
        agent_client = AgentClient()
        agent_client.set_token(access_token)
        info = agent_client.check_agent_exists()
        print(f"[Diagnostics] Agent check OK: {info}")
        return jsonify({
            "code": 200,
            "msg": "Agent 配置正常",
            "data": info,
        })
    except Exception as e:
        error_body = str(e)
        if hasattr(e, "response") and e.response is not None:
            try:
                error_body = e.response.text()
            except Exception:
                try:
                    error_body = str(e.response)
                except Exception:
                    pass
        print(f"[Diagnostics] Agent check FAILED: {error_body}")
        return jsonify({
            "code": 500,
            "msg": f"Agent 检查失败",
            "data": {
                "error_type": type(e).__name__,
                "error_detail": error_body,
                "agent_name": os.getenv("AGENT_NAME"),
                "agent_version": os.getenv("AGENT_VERSION", "latest"),
                "endpoint": (
                    f"https://{os.getenv('FOUNDRY_ACCOUNT_NAME')}.services.ai.azure.com"
                    f"/api/projects/{os.getenv('FOUNDRY_PROJECT_NAME')}"
                ),
            },
        }), 500


# ---------------------------------------------------------------------------
# Chat API
# ---------------------------------------------------------------------------
@app.route("/api/ai/chat", methods=["POST"])
def invoke_agent():
    access_token = session.get(SESSION_TOKEN_KEY)
    if not access_token:
        return jsonify({"code": 401, "msg": "未登录，请先完成 OAuth 登录", "data": None}), 401

    body = request.get_json(silent=True)
    if not body or "messages" not in body or not body["messages"]:
        return jsonify({"code": 400, "msg": "请求体缺少 messages 字段", "data": None}), 400

    content = body["messages"][0].get("content", "")
    if not content:
        return jsonify({"code": 400, "msg": "消息内容为空", "data": None}), 400

    try:
        agent_client = AgentClient()
        agent_client.set_token(access_token)
        reply = agent_client.send_message(content)
        print(f"[API] Agent reply ({len(reply)} chars)")
        return jsonify({"code": 200, "msg": "成功", "data": reply})
    except Exception as e:
        # Capture as much detail as possible
        error_body = str(e)
        if hasattr(e, "response") and e.response is not None:
            try:
                error_body = e.response.text()
            except Exception:
                try:
                    error_body = str(e.response)
                except Exception:
                    pass

        print(f"[API] Agent call failed:\n  {type(e).__name__}: {error_body}")
        return jsonify({
            "code": 500,
            "msg": f"调用 Agent 失败：{error_body[:500]}",
            "data": None,
        }), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
