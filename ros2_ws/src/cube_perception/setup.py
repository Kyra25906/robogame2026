from glob import glob
from setuptools import find_packages, setup
name="cube_perception"
setup(name=name,version="0.1.0",packages=find_packages(),
data_files=[
    ("share/ament_index/resource_index/packages",["resource/"+name]),
    ("share/"+name,["package.xml"]),
    ("share/"+name+"/config", glob("config/*.json")),
],
install_requires=[],zip_safe=True,maintainer="RoboGame Team",maintainer_email="team@example.com",
description="HSV cube detector",license="MIT",
entry_points={"console_scripts":[
    "cube_perception = cube_perception.node:main",
    "mock_perception = cube_perception.mock_node:main",
    "cube_detector = cube_perception.standalone:main",
    "hsv_tuner = cube_perception.hsv_tuner:main",
    "vision_report = cube_perception.report:main",
]})
