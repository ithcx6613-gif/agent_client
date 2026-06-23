from flask import Flask, request, session, jsonify, render_template, redirect
from auth_helper import AuthHelper
from agent_client import AgentClient
from flask_cors import CORS

# 初始化AuthHelper
auth_helper = AuthHelper()
# -------------------------- 1. 配置参数（替换为你的实际值） --------------------------
app = Flask(__name__, )
CORS(app, supports_credentials=True)  # 新增：允许跨域，支持凭证
# 重要：Flask的session密钥，用于存储auth_code_flow（防CSRF），生产环境需改为随机安全值
app.secret_key = "123456"

# 关键修复：确保session cookie在跨站重定向时也能发送
# SameSite=None + Secure 允许跨站cookie，但生产环境需要HTTPS
# 开发环境用 Lax（默认）通常够用，但显式设置更安全
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_REFRESH_EACH_REQUEST"] = True

# -------------------------- 2. 生成授权URL（引导用户授权） --------------------------
@app.route("/")
def index():
    # 第一步：根路由：返回前端的index.html页面
    return render_template("index.html")  # 自动读取templates/index.html


@app.route("/auth/url")
def get_auth_url():
    auth_uri = auth_helper.interactive_login()
    return jsonify({
        "code": 200,
        "data": {"auth_url": auth_uri}
    })

# -------------------------- 3. 回调接口：提取授权码并兑换令牌 --------------------------
@app.route("/callback")
def callback():
    # 关键修复4：先打印session和state，便于调试
    print("=== Callback调试信息 ===")
    print("请求中的state:", request.args.get("state"))
    print("Session中的flow:", session.get("auth_code_flow", {}).get("state"))
    
    # 原有错误处理逻辑
    if "error" in request.args:
        print(f"授权失败：{request.args['error']} - {request.args.get('error_description', '')}")
        return redirect(f"/?error={request.args['error']}")

    auth_code = request.args.get("code")
    if not auth_code:
        print("❌ 未获取到授权码，请重新授权")
        return redirect("/?error=未获取到授权码")
    
    # 关键修复5：增加flow存在性检查，友好提示
    auth_code_flow = session.get("auth_code_flow")
    if not auth_code_flow:
        print("❌ Session中无auth_code_flow，可能是授权流程过期/跨域")
        return redirect("/?error=授权流程过期（Session丢失），请重新点击授权按钮")
    
    # 关键修复6：手动验证state（提前拦截不匹配）
    request_state = request.args.get("state")
    flow_state = auth_code_flow.get("state")
    if request_state != flow_state:
        print(f"❌ State不匹配：请求={request_state}，Session={flow_state}")
        return redirect(f"/?error=State验证失败（CSRF风险），请重新授权")
    
    # 原有兑换令牌逻辑
    try:
        result = auth_helper.app.acquire_token_by_auth_code_flow(
            auth_code_flow=auth_code_flow,
            auth_response=request.args
        )
    except ValueError as e:
        print(f"❌ 兑换令牌失败：{str(e)}")
        return redirect(f"/?error=授权流程异常：{str(e)}")

    if "access_token" in result:
        access_token = result["access_token"]
        expires_in = result["expires_in"]
        token_type = result["token_type"]
        print(f"✅ 获取用户 TC Token: ", access_token)
        # 清空已使用的flow，避免重复使用
        session.pop("auth_code_flow", None)
        return redirect(f"/?access_token={access_token}&expires_in={expires_in}&token_type={token_type}")
    else:
        print("❌ 获取用户TC Token 失败")
        error_msg = result.get('error', '兑换令牌失败')
        return redirect(f"/?error={error_msg}")

@app.route("/api/ai/chat", methods=["POST"])
def invoke_agent():
    # try:
        # 从请求头中提取Token
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return jsonify({
                "code": 401,
                "msg": "未提供有效的Token",
                "data": None
            }), 401
        access_token = auth_header.split(" ")[1]
        print(f"request auth access token: {access_token[:50]}")
        if not access_token:
            return jsonify({
                "code": 401,
                "msg": "未提供有效的Token",
                "data": None
            }), 401
        # 尝试获取Agent状态（需要OBO权限）
        agent_client = AgentClient()
        agent_client.set_static_sts_token(access_token)
        # agent_status = agent_client.send_message_to_agent(payload)
        message = request.get_json(silent=True).get('messages')[0].get('content')
        agent_status = agent_client.send_message_to_agent(message)
        print(f"Agent状态: {agent_status}")
        return jsonify({
            "code": 200,
            "msg": "成功获取Agent状态",
            "data": agent_status
        })
    # except Exception as e:
    #     print(f"访问Agent服务失败: {str(e)}")
    #     # 检查是否需要管理员同意
    #     if "consent_required" in str(e).lower():
    #         print("错误提示：可能需要管理员同意某些权限")
    #         return jsonify({
    #             "code": 403,
    #             "msg": "需要管理员同意相关权限",
    #             "data": None
    #         }), 403
    #     return jsonify({
    #         "code": 500,
    #         "msg": f"调用Agent失败：{str(e)}",
    #         "data": None
    #     }), 500
    


# -------------------------- 启动Web服务 --------------------------
if __name__ == "__main__":
    # 运行在5000端口，允许外部访问（测试用）
    app.run(host="0.0.0.0", port=5000, debug=True)
