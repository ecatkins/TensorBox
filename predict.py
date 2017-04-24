"""
This file is designed for prediction of bounding boxes for a single image.

Predictions could be made in two ways: command line style or service style. Command line style denotes that one can 
run this script from the command line and configure all options right in the command line. Service style allows 
to call :func:`initialize` function once and call :func:`hot_predict` function as many times as it needed to. 
     
"""

import tensorflow as tf
import os, json, subprocess
from optparse import OptionParser

from scipy.misc import imread, imresize
from PIL import Image, ImageDraw

from train import build_forward
from utils.annolist import AnnotationLib as al
from utils.train_utils import add_rectangles, rescale_boxes


def initialize(weights_path, hypes_path, options=None):
    """Initialize prediction process.
     
    All long running operations like TensorFlow session start and weights loading are made here.
     
    Args:
        weights_path (string): The path to the model weights file. 
        hypes_path (string): The path to the hyperparameters file. 
        options (dict): The options dictionary with parameters for the initialization process.

    Returns (dict):
        The dict object which contains `sess` - TensorFlow session, `pred_boxes` - predicted boxes Tensor, 
          `pred_confidences` - predicted confidences Tensor, `x_in` - input image Tensor, 
          `hypes` - hyperparametets dictionary.
    """

    H = prepare_options(hypes_path, options)

    tf.reset_default_graph()
    x_in = tf.placeholder(tf.float32, name='x_in', shape=[H['image_height'], H['image_width'], 3])
    if H['use_rezoom']:
        pred_boxes, pred_logits, pred_confidences, pred_confs_deltas, pred_boxes_deltas \
            = build_forward(H, tf.expand_dims(x_in, 0), 'test', reuse=None)
        grid_area = H['grid_height'] * H['grid_width']
        pred_confidences = tf.reshape(
            tf.nn.softmax(tf.reshape(pred_confs_deltas, [grid_area * H['rnn_len'], H['num_classes']])),
            [grid_area, H['rnn_len'], H['num_classes']])
        if H['reregress']:
            pred_boxes = pred_boxes + pred_boxes_deltas
    else:
        pred_boxes, pred_logits, pred_confidences = build_forward(H, tf.expand_dims(x_in, 0), 'test', reuse=None)

    saver = tf.train.Saver()
    sess = tf.Session()
    sess.run(tf.global_variables_initializer())
    saver.restore(sess, weights_path)
    return {'sess': sess, 'pred_boxes': pred_boxes, 'pred_confidences': pred_confidences, 'x_in': x_in, 'hypes': H}


def hot_predict(image_path, init_params, to_json=False):
    """Makes predictions when all long running preparation operations are made. 
    
    Args:
        image_path (string): The path to the source image. 
        init_params (dict): The parameters produced by :func:`initialize`.

    Returns (Annotation):
        The annotation for the source image.
    """

    H = init_params['hypes']
    options = H['evaluate']  # The options for more precise prediction of bounding boxes.

    # predict
    orig_img = imread(image_path, mode = 'RGB')[:, :, :3]
    img = imresize(orig_img, (H['image_height'], H['image_width']), interp='cubic')
    (np_pred_boxes, np_pred_confidences) = init_params['sess'].\
        run([init_params['pred_boxes'], init_params['pred_confidences']], feed_dict={init_params['x_in']: img})
    pred_anno = al.Annotation()
    pred_anno.imageName = image_path
    _, rects = add_rectangles(H, [img], np_pred_confidences, np_pred_boxes, use_stitching=True,
                              rnn_len=H['rnn_len'], min_conf=options['min_conf'], tau=options['tau'],
                              show_suppressed=options['show_suppressed'])

    pred_anno.rects = [r for r in rects if r.x1 < r.x2 and r.y1 < r.y2]
    pred_anno.imagePath = os.path.abspath(image_path)
    pred_anno = rescale_boxes((H['image_height'], H['image_width']), pred_anno, orig_img.shape[0], orig_img.shape[1])
    result = [r.writeJSON() for r in pred_anno] if to_json else pred_anno
    return result


def prepare_options(hypes_path='hypes.json', options=None):
    """Sets parameters of the prediction process.
        
    Args:
        hypes_path (string): The path to model hyperparameters file.
        options (dict): The command line options to set before start predictions.

    Returns (dict):
        The model hyperparameters dictionary.
    """

    with open(hypes_path, 'r') as f:
        H = json.load(f)

    # set default options values if they were not provided
    if options is None:
        if 'evaluate' in H:
            options = H['evaluate']
        else:
            print ('Evaluate parameters were not found! You can provide them through hyperparameters json file '
                   'or hot_predict options parameter.')
            return None
    else:
        H['evaluate'] = options

    os.environ['CUDA_VISIBLE_DEVICES'] = str(options['gpu'])
    H['batch_size'] = 1

    return H


def save_results(image_path, anno):
    """Saves results of the prediction.
    
    Args:
        image_path (string): The path to source image to predict bounding boxes.
        anno (Annotation): The predicted annotations for source image.

    Returns: 
        Nothing.
    """
    # draw
    
    try:
    	new_img = Image.open(image_path)
    	d = ImageDraw.Draw(new_img)
    	for r in anno.rects:
            d.rectangle([r.left(), r.top(), r.right(), r.bottom()], outline=(255, 0, 0))
    except:
	print("FAILED to draw")
    # save
    try:
        fpath = os.path.join(os.path.dirname(image_path), 'result.png')
        new_img.save(fpath)
        subprocess.call(['chmod', '777', fpath])
    except:
        try:
            fpath = os.path.join(os.path.dirname(image_path), 'result.png')
            new_img.convert('RGB').save(fpath)
            subprocess.call(['chmod', '777', fpath])
        except:
            print("Failed to save image")
    try:
        fpath = os.path.join(os.path.dirname(image_path), 'result.json')
        al.saveJSON(fpath, anno)
        subprocess.call(['chmod', '777', fpath])
    except:
        print("Failed to save json")


def main():
    parser = OptionParser(usage='usage: %prog [options] <image> <weights> <hypes>')
    parser.add_option('--multi',action = 'store_true', dest = 'multi', default = False)
    parser.add_option('--gpu', action='store', type='int', default=0)
    parser.add_option('--tau', action='store', type='float',  default=0.25)
    parser.add_option('--min_conf', action='store', type='float', default=0.2)
    parser.add_option('--show_suppressed', action='store_true', dest='show_suppressed', default=False)
    
    (options, args) = parser.parse_args()
    if len(args) < 3:
        print ('Provide image, weights and hypes paths')
        return

    options_dict = options.__dict__
    init_params = initialize(args[1], args[2], options.__dict__)
    if options_dict['multi'] == True:
        image_list = json.loads(args[0])
        for i_path in image_list:
            print(i_path)
            pred_anno = hot_predict(i_path, init_params)
            save_results(i_path, pred_anno)
    else:
        pred_anno = hot_predict(args[0], init_params)
        save_results(args[0], pred_anno)


if __name__ == '__main__':
    main()
