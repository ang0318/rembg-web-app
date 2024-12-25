from flask import Flask, request, send_file, jsonify, make_response, send_from_directory
from flask_cors import CORS
from rembg import remove
from PIL import Image, ImageOps
import io
import os
from dotenv import load_dotenv
import hashlib
import numpy as np
from colorsys import rgb_to_hsv

app = Flask(__name__)
CORS(app, supports_credentials=True)

# 加载.env文件
try:
    load_dotenv()
except Exception as e:
    print(f"Warning: Failed to load .env file: {e}")
    # 使用默认密码
    os.environ['APP_PASSWORD'] = '080318'
    os.environ['SALT'] = 'default_salt'

# 从.env文件获取密码和环境配置
APP_PASSWORD = os.getenv('APP_PASSWORD', 'default_password')
SALT = os.getenv('SALT', 'default_salt')
DEV_MODE = os.getenv('DEV_MODE', 'False').lower() == 'true'
ENABLE_AUTH = os.getenv('ENABLE_AUTH', 'True').lower() == 'true'  # 新增: 是否启用密码验证

def hash_password(password_hash, salt):
    """将已经哈希过的密码与盐值再次哈希"""
    if DEV_MODE:
        # 开发环境下直接比较明文密码
        return password_hash
    return hashlib.sha256(f"{password_hash}{salt}".encode()).hexdigest()

# 计算正确的最终哈希值
if DEV_MODE:
    CORRECT_HASH = APP_PASSWORD  # 开发环境使用明文密码
else:
    PASSWORD_HASH = hashlib.sha256(APP_PASSWORD.encode()).hexdigest()  # 先对原始密码进行哈希
    CORRECT_HASH = hash_password(PASSWORD_HASH, SALT)  # 再与盐值组合哈希

# 添加根路由，返回index.html
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/index.html')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/styles.css')
def serve_css():
    return send_from_directory('.', 'styles.css')
@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)

@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('.', 'manifest.json')

@app.route('/get-salt', methods=['GET'])
def get_salt():
    """返回盐值给前端"""
    return jsonify({'salt': SALT})

@app.route('/verify-password', methods=['POST'])
def verify_password():
    """验证密码"""
    # 如果未启用密码验证，直接返回成功
    if not ENABLE_AUTH:
        response = make_response(jsonify({'status': 'success'}))
        response.set_cookie(
            'auth_token',
            value='no_auth_needed',
            max_age=30*24*60*60,
            httponly=True,
            secure=not DEV_MODE,
            samesite='Lax'
        )
        return response

    password_hash = request.json.get('password_hash')
    if not password_hash:
        return jsonify({'status': 'error', 'message': '无效的请求'}), 400
    
    if DEV_MODE:
        # 开发环境：直接比较明文密码
        if password_hash == APP_PASSWORD:
            # 密码正确，生成cookie值（明文密码+盐值的哈希）
            cookie_hash = hashlib.sha256(f"{APP_PASSWORD}{SALT}".encode()).hexdigest()
            response = make_response(jsonify({'status': 'success'}))
            response.set_cookie(
                'auth_token',
                value=cookie_hash,
                max_age=30*24*60*60,
                httponly=True,
                secure=False,  # 开发环境不使用secure
                samesite='Lax'
            )
            return response
    else:
        # 生产环境：比较哈希值
        stored_hash = hashlib.sha256(APP_PASSWORD.encode()).hexdigest()
        if password_hash == stored_hash:
            # 密码正确，生成cookie值（明文密码+盐值的哈希）
            cookie_hash = hashlib.sha256(f"{APP_PASSWORD}{SALT}".encode()).hexdigest()
            response = make_response(jsonify({'status': 'success'}))
            response.set_cookie(
                'auth_token',
                value=cookie_hash,
                max_age=30*24*60*60,
                httponly=True,
                secure=True,
                samesite='Lax'
            )
            return response
    
    return jsonify({'status': 'error', 'message': '密码错误'}), 401

@app.route('/check-auth', methods=['GET'])
def check_auth():
    """检查认证状态"""
    if not ENABLE_AUTH:
        return jsonify({'status': 'success'})

    auth_token = request.cookies.get('auth_token')
    # 验证cookie（比较cookie值是否为明文密码+盐值的哈希）
    expected_hash = hashlib.sha256(f"{APP_PASSWORD}{SALT}".encode()).hexdigest()
    
    if auth_token == expected_hash or auth_token == 'no_auth_needed':
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error', 'message': '未认证'}), 401

def color_distance(color1, color2):
    """计算两个颜色之间的距离"""
    r1, g1, b1 = color1
    r2, g2, b2 = color2
    
    # 使用更简单的颜色距离计算
    max_distance = max(abs(r1-r2), abs(g1-g2), abs(b1-b2))
    return max_distance

def create_color_mask(image, target_color, threshold):
    """创建基于颜色的蒙版"""
    img_array = np.array(image)
    mask = np.zeros((image.height, image.width), dtype=np.uint8)
    
    for y in range(image.height):
        for x in range(image.width):
            pixel = img_array[y, x][:3]  # 获取RGB值
            if color_distance(target_color, pixel) <= threshold/100:
                mask[y, x] = 0  # 透明
            else:
                mask[y, x] = 255  # 不透明
    
    return Image.fromarray(mask)

@app.route('/remove-bg', methods=['POST'])
def remove_background():
    # 验证哈希值
    if ENABLE_AUTH:  # 只在启用密码验证时检查
        auth_token = request.cookies.get('auth_token')
        expected_hash = hashlib.sha256(f"{APP_PASSWORD}{SALT}".encode()).hexdigest()
        if auth_token != expected_hash and auth_token != 'no_auth_needed':
            return jsonify({'status': 'error', 'message': '未授权访问'}), 401

    if 'file' not in request.files:
        return jsonify({'error': '没有文件上传'}), 400

    try:
        file = request.files['file']
        matting_mode = request.form.get('matting_mode', 'false')
        
        input_image = Image.open(file.stream)
        
        if matting_mode == 'color':
            # 颜色抠图模式
            target_color = request.form.get('target_color', '#ffffff')
            color_threshold = int(request.form.get('color_threshold', 30))  # 改为整数
            background_type = request.form.get('background_type', 'transparent')
            background_color = request.form.get('background_color', '#ffffff')
            
            # 将原图转换为RGBA
            if input_image.mode != 'RGBA':
                input_image = input_image.convert('RGBA')
            
            # 获取图片数据
            img_array = np.array(input_image)
            
            # 获取目标颜色的RGB值
            try:
                r1, g1, b1 = [int(target_color[i:i+2], 16) for i in (1, 3, 5)]
                target_rgb = (r1, g1, b1)
            except ValueError as e:
                return jsonify({'error': '无效的目标颜色格式'}), 400
            
            # 创建输出数组
            output_array = img_array.copy()
            
            # 遍历每个像素进行颜色匹配
            for y in range(img_array.shape[0]):
                for x in range(img_array.shape[1]):
                    pixel = img_array[y, x][:3]
                    distance = color_distance(target_rgb, pixel)
                    
                    # 根据颜色距离和阈值决定是否透明
                    if distance <= color_threshold:  # 直接使用阈值，不需要缩放
                        if background_type == 'transparent':
                            output_array[y, x, 3] = 0  # 完全透明
                        else:
                            try:
                                # 设置为背景色
                                r2, g2, b2 = [int(background_color[i:i+2], 16) for i in (1, 3, 5)]
                                output_array[y, x] = [r2, g2, b2, 255]
                            except ValueError:
                                return jsonify({'error': '无效的背景颜色格式'}), 400
            
            try:
                output_image = Image.fromarray(output_array, 'RGBA')
            except Exception as e:
                return jsonify({'error': f'图片处理失败: {str(e)}'}), 500
                
        else:
            # 原有rembg处理逻辑
            alpha_matting = matting_mode == 'true'
            alpha_matting_foreground_threshold = int(request.form.get('alpha_matting_foreground_threshold', 50))
            alpha_matting_background_threshold = int(request.form.get('alpha_matting_background_threshold', 50))
            edge_blur = int(request.form.get('edge_blur', 0))
            only_mask = request.form.get('only_mask', 'false') == 'true'
            
            output_image = remove(
                input_image,
                alpha_matting=alpha_matting,
                alpha_matting_foreground_threshold=alpha_matting_foreground_threshold,
                alpha_matting_background_threshold=alpha_matting_background_threshold,
                edge_blur=edge_blur,
                only_mask=only_mask
            )
            
            # 处��背景色
            background_type = request.form.get('background_type', 'transparent')
            background_color = request.form.get('background_color', '#ffffff')
            if background_type == 'color' and not only_mask:
                background = Image.new('RGBA', output_image.size, background_color)
                background.paste(output_image, (0, 0), output_image)
                output_image = background
        
        img_byte_arr = io.BytesIO()
        output_image.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        
        return send_file(
            img_byte_arr,
            mimetype='image/png'
        )
    
    except Exception as e:
        return str(e), 500

@app.route('/get-env-config', methods=['GET'])
def get_env_config():
    """返回环境配置信息"""
    print(f"Current DEV_MODE: {DEV_MODE}")  # 添加调试输出
    print(f"Current ENABLE_AUTH: {ENABLE_AUTH}")  # 添加调试输出
    return jsonify({
        'devMode': DEV_MODE,
        'enableAuth': ENABLE_AUTH
    })

@app.route('/reload-config', methods=['POST'])
def reload_config():
    """重新加载环境配置"""
    global DEV_MODE, ENABLE_AUTH
    
    # 强制重新加载 .env 文件
    load_dotenv(override=True)
    
    # 重新读取配置
    DEV_MODE = os.getenv('DEV_MODE', 'False').lower() == 'true'
    ENABLE_AUTH = os.getenv('ENABLE_AUTH', 'True').lower() == 'true'
    
    print(f"Reloaded config - DEV_MODE: {DEV_MODE}, ENABLE_AUTH: {ENABLE_AUTH}")
    
    return jsonify({
        'devMode': DEV_MODE,
        'enableAuth': ENABLE_AUTH
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)