from flask import Flask, Response, send_file, json, request, copy_current_request_context
from io import BytesIO
import base64
import time
from PIL import Image, ImageChops
import tempfile
import logging
from datetime import datetime
import os
import picamera
from flask_cors import CORS, cross_origin
from flask_socketio import SocketIO, emit
import threading
import math
from stats import standard_deviation

RUN_CAM = False
ENTROPY_SAMPLE = []


app = Flask(__name__)
CORS(app)
socketio = SocketIO(app)

def histogram_entropy(histogram):
    histogram_length = sum(histogram[:256])
    samples_probability = [float(h) / histogram_length for h in histogram]
    entropy = -sum([p * math.log(p, 2) * p for p in samples_probability if p != 0])
    return entropy

def image_entropy(img):
    global ENTROPY_SAMPLE
    histogram = img.histogram()
    r = histogram[:256]
    g = histogram[256:512]
    b = histogram[512:]
    all_band_entropy = histogram_entropy(histogram)
    r_entropy = histogram_entropy(r)
    g_entropy = histogram_entropy(g)
    b_entropy = histogram_entropy(b)
    entropy_obj = {
        'total_entropy': all_band_entropy,
        'r_entropy': r_entropy,
        'g_entropy': g_entropy,
        'b_entropy': b_entropy,
    }
    ENTROPY_SAMPLE.append(entropy_obj['total_entropy'])
    return (
        {
            'r': r,
            'g': g,
            'b': b
        },
        entropy_obj
    )

def analyze_images(img_path_1, img_path_2):
    img1 = Image.open(img_path_1)
    img2 = Image.open(img_path_2)
    path = get_temp_path('diff')
    img_diff = ImageChops.difference(img1, img2)
    img_diff.save(path)
    img_str = open_image(path)
    histogram, entropy = image_entropy(img_diff)
    return histogram, entropy, img_str

def get_temp_path(name):
    tempdir = tempfile.mkdtemp()
    date = datetime.now()
    filename = '{}-{}.jpg'.format(name, date)
    path = os.path.join(tempdir, filename)
    return path

def open_image(path):
    img_str = ''
    with open(path, "rb") as imageFile:
        img_str = 'data:image/jpeg;base64,' + base64.b64encode(imageFile.read())
    return img_str
        

def snap():
    img_str = ''
    camera = picamera.PiCamera()
    
    try:
        path = get_temp_path('capture')
        camera.capture(path)
        img_str = open_image(path)
    except (ValueError, RuntimeError, TypeError, NameError):
        logging.exception("could not capture image")
    finally:
        camera.close()
    return  (img_str, path)

@app.route('/take')
def take_picture():
    img_str, _ = snap()
    response = Response(
        response = json.dumps({
            'src': img_str
        }),
        status = 200,
        mimetype='application/json'
    )
    return response

@socketio.on('motion')
def check_motion(message):
    logging.debug(message)
    global RUN_CAM
    response = None
    @copy_current_request_context
    def run_motion_cam(emit_func):
        img_1_str = None
        img_1_path = None
        while RUN_CAM:
            img_2_str = None
            img_2_path = None
            if not img_1_str or not img_1_path:
                img_1_str, img_1_path = snap()
                emit_func( "detector running", {'pic': img_2_str, 'diff_img': '', 'entropy': '', 'histogram': {}})
                time.sleep(0.5)
                img_2_str, img_2_path = snap()
                histogram, entropy, diff_img = analyze_images(img_1_path, img_2_path)
                std_dev_thread = threading.Thread(target=emit_std_dev, args=[emit])
                std_dev_thread.start()

                emit_func( "detector running", {'pic': img_2_str, 'diff_img': diff_img, 'entropy': entropy, 'histogram': histogram})
            else:
                img_2_str, img_2_path = snap()
                histogram, entropy, diff_img = analyze_images(img_1_path, img_2_path)
                emit_func( "detector running", {'pic': img_2_str, 'diff_img': diff_img, 'entropy': entropy, 'histogram': histogram})
                std_dev_thread = threading.Thread(target=emit_std_dev, args=[emit])
                std_dev_thread.start()

                img_1_str = img_2_str
                img_1_path = img_2_path
            time.sleep(0.5)
    @copy_current_request_context
    def emit_std_dev(emit_func):
        mean, sample_variance, sample_std_dev = standard_deviation(ENTROPY_SAMPLE)
        emit_func("standard-dev", {'mean': mean, 'sample_variance': sample_variance, 'sample_std_dev': sample_std_dev})
        
    def run_detector(run_cam, emit_func):
        logging.debug(run_cam)
        cam_thread = None

        if run_cam:
            cam_thread = threading.Thread(target=run_motion_cam, args=[emit_func])
            cam_thread.start()
        else:
            if cam_thread:
                logging.debug('stopping thread')
                cam_thread.stop()
                
    if message == 'on':
        RUN_CAM = True
        response = "on"
    else:
        RUN_CAM = False
        response = "off"
    logging.debug(response)
    emit('motion response', {'data': response})
    run_detector(RUN_CAM, emit)

@socketio.on('disconnect')
def stop_detector():
    global RUN_CAM
    RUN_CAM = False
        

if __name__ == "__main__":
    socketio.run(app, debug=True, host='0.0.0.0', port=80)
