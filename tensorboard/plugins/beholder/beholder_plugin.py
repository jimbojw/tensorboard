# Copyright 2017 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import time

import numpy as np
import tensorflow as tf
from google.protobuf import message
from werkzeug import wrappers

from tensorboard.backend import http_util
from tensorboard.backend.event_processing import plugin_asset_util as pau
from tensorboard.plugins import base_plugin
from tensorboard.plugins.beholder import file_system_tools
from tensorboard.plugins.beholder import im_util
from tensorboard.plugins.beholder import shared_config


class BeholderPlugin(base_plugin.TBPlugin):
  """
  TensorBoard plugin for viewing model data as a live video during training.
  """

  plugin_name = shared_config.PLUGIN_NAME

  def __init__(self, context):
    self._MULTIPLEXER = context.multiplexer
    self.PLUGIN_LOGDIR = pau.PluginDirectory(
        context.logdir, shared_config.PLUGIN_NAME)
    self.FPS = 10
    self.most_recent_frame = im_util.get_image_relative_to_script('no-data.png')
    self.most_recent_info = [{
        'name': 'Waiting for data...',
    }]

    if not tf.gfile.Exists(self.PLUGIN_LOGDIR):
      tf.gfile.MakeDirs(self.PLUGIN_LOGDIR)
      file_system_tools.write_pickle(
          shared_config.DEFAULT_CONFIG,
          '{}/{}'.format(self.PLUGIN_LOGDIR, shared_config.CONFIG_FILENAME))


  def get_plugin_apps(self):
    return {
        '/change-config': self._serve_change_config,
        '/beholder-frame': self._serve_beholder_frame,
        '/section-info': self._serve_section_info,
        '/ping': self._serve_ping,
        '/tags': self._serve_tags,
        '/is-active': self._serve_is_active,
    }


  def is_active(self):
    summary_filename = '{}/{}'.format(
        self.PLUGIN_LOGDIR, shared_config.SUMMARY_FILENAME)
    info_filename = '{}/{}'.format(
        self.PLUGIN_LOGDIR, shared_config.SECTION_INFO_FILENAME)
    return tf.gfile.Exists(summary_filename) and\
           tf.gfile.Exists(info_filename)


  @wrappers.Request.application
  def _serve_is_active(self, request):
    return http_util.Respond(request,
                             {'is_active': self.is_active()},
                             'application/json')


  def _fetch_current_frame(self):
    path = '{}/{}'.format(self.PLUGIN_LOGDIR, shared_config.SUMMARY_FILENAME)

    try:
      frame = file_system_tools.read_tensor_summary(path).astype(np.uint8)
      self.most_recent_frame = frame
      return frame

    except (message.DecodeError, IOError, tf.errors.NotFoundError):
      return self.most_recent_frame


  @wrappers.Request.application
  def _serve_tags(self, request):
    if self.is_active:
      runs_and_tags = {
          'plugins/{}'.format(shared_config.PLUGIN_NAME): {
              'tensors': [shared_config.TAG_NAME]
          }
      }
    else:
      runs_and_tags = {}

    return http_util.Respond(request,
                             runs_and_tags,
                             'application/json')


  @wrappers.Request.application
  def _serve_change_config(self, request):
    config = {}

    for key, value in request.form.items():
      try:
        config[key] = int(value)
      except ValueError:
        if value == 'false':
          config[key] = False
        elif value == 'true':
          config[key] = True
        else:
          config[key] = value

    self.FPS = config['FPS']

    file_system_tools.write_pickle(
        config,
        '{}/{}'.format(self.PLUGIN_LOGDIR, shared_config.CONFIG_FILENAME))
    return http_util.Respond(request, {'config': config}, 'application/json')


  @wrappers.Request.application
  def _serve_section_info(self, request):
    path = '{}/{}'.format(
        self.PLUGIN_LOGDIR, shared_config.SECTION_INFO_FILENAME)
    info = file_system_tools.read_pickle(path, default=self.most_recent_info)
    self.most_recent_info = info
    return http_util.Respond(request, info, 'application/json')


  def _frame_generator(self):

    while True:
      last_duration = 0

      if self.FPS == 0:
        continue
      else:
        time.sleep(max(0, 1/(self.FPS) - last_duration))

      start_time = time.time()
      array = self._fetch_current_frame()
      image_bytes = im_util.encode_png(array)

      frame_text = b'--frame\r\n'
      content_type = b'Content-Type: image/png\r\n\r\n'

      response_content = frame_text + content_type + image_bytes + b'\r\n\r\n'

      last_duration = time.time() - start_time
      yield response_content


  @wrappers.Request.application
  def _serve_beholder_frame(self, request): # pylint: disable=unused-argument
    # Thanks to Miguel Grinberg for this technique:
    # https://blog.miguelgrinberg.com/post/video-streaming-with-flask
    mimetype = 'multipart/x-mixed-replace; boundary=frame'
    return wrappers.Response(response=self._frame_generator(),
                             status=200,
                             mimetype=mimetype)

  @wrappers.Request.application
  def _serve_ping(self, request): # pylint: disable=unused-argument
    return http_util.Respond(request, {'status': 'alive'}, 'application/json')
