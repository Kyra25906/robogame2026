from glob import glob
from setuptools import find_packages, setup

name="robogame_bringup"
setup(name=name,version="0.1.0",packages=find_packages(),
data_files=[("share/ament_index/resource_index/packages",["resource/"+name]),
            ("share/"+name,["package.xml"]),
            ("share/"+name+"/launch",glob("launch/*.launch.py")),
            ("share/"+name+"/config",glob("config/*.yaml"))],
install_requires=[],zip_safe=True,maintainer="RoboGame Team",maintainer_email="team@example.com",
description="RoboGame bringup",license="MIT")

