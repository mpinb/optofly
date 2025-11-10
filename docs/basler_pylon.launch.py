# Copyright (c) 2024 Fraunhofer IOSB and contributors
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#
#    * Redistributions in binary form must reproduce the above copyright
#      notice, this list of conditions and the following disclaimer in the
#      documentation and/or other materials provided with the distribution.
#
#    * Neither the name of the Fraunhofer IOSB nor the names of its
#      contributors may be used to endorse or promote products derived from
#      this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import os

from ament_index_python import get_package_share_directory

import launch
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node

os.environ['RCUTILS_CONSOLE_OUTPUT_FORMAT'] = '{time}: [{name}] [{severity}]\t{message}'


def generate_launch_description():
    # Get default camera_info path from package
    default_camera_info_path = os.path.join(
        get_package_share_directory('camera_aravis2'),
        'config/camera_info_example_uv.yaml'
    )

    # Declare launch arguments
    guid_arg = DeclareLaunchArgument(
        'guid',
        default_value='',
        description='Camera GUID (serial number or IP address). Leave empty to connect to any available camera.'
    )

    camera_type_arg = DeclareLaunchArgument(
        'camera_type',
        default_value='uv',
        description='Camera connection type: "uv" for USB3Vision or "gv" for GigEVision'
    )

    camera_name_arg = DeclareLaunchArgument(
        'camera_name',
        default_value='basler_camera',
        description='Name of the camera node'
    )

    frame_id_arg = DeclareLaunchArgument(
        'frame_id',
        default_value='camera',
        description='Frame ID for the camera'
    )

    pixel_format_arg = DeclareLaunchArgument(
        'pixel_format',
        default_value='Mono8',
        description='Pixel format (e.g., Mono8, Mono12, BayerRG8, RGB8)'
    )

    exposure_time_arg = DeclareLaunchArgument(
        'exposure_time',
        default_value='10000.0',
        description='Exposure time in microseconds'
    )

    frame_rate_arg = DeclareLaunchArgument(
        'frame_rate',
        default_value='30.0',
        description='Acquisition frame rate in Hz'
    )

    # Create the camera driver node - executable changes based on camera_type
    basler_camera_node = Node(
        name=LaunchConfiguration('camera_name'),
        package='camera_aravis2',
        executable=PythonExpression(['"camera_driver_', LaunchConfiguration('camera_type'), '"']),
        output='screen',
        emulate_tty=True,
        parameters=[{
            # Driver-specific parameters
            'guid': LaunchConfiguration('guid'),
            'frame_id': LaunchConfiguration('frame_id'),
            'camera_info_urls': [default_camera_info_path],

            # GenICam-specific parameters for Basler cameras
            'DeviceControl': {
                'DeviceLinkThroughputLimitMode': 'Off'
            },
            'TransportLayerControl': {
                'GevSCPSPacketSize': 9000,
                'GevSCPD': 0
            },
            'ImageFormatControl': {
                'PixelFormat': LaunchConfiguration('pixel_format')
            },
            'AcquisitionControl': {
                'AcquisitionMode': 'Continuous',
                'ExposureMode': 'Timed',
                'ExposureAuto': 'Off',
                'ExposureTime': LaunchConfiguration('exposure_time'),
                'AcquisitionFrameRateEnable': True,
                'AcquisitionFrameRate': LaunchConfiguration('frame_rate')
            },
            'AnalogControl': {
                'GainAuto': 'Off',
                'Gain': {
                    'All': 0.0
                }
            }
        }]
    )

    return launch.LaunchDescription([
        guid_arg,
        camera_type_arg,
        camera_name_arg,
        frame_id_arg,
        pixel_format_arg,
        exposure_time_arg,
        frame_rate_arg,
        basler_camera_node
    ])
