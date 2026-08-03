from setuptools import find_packages, setup
name="localization"
setup(name=name,version="0.1.0",packages=find_packages(),
data_files=[("share/ament_index/resource_index/packages",["resource/"+name]),("share/"+name,["package.xml"])],
install_requires=[],zip_safe=True,maintainer="RoboGame Team",maintainer_email="team@example.com",
description="Wheel odometry and IMU localization",license="MIT",
entry_points={"console_scripts":["localization_node = localization.node:main"]})

