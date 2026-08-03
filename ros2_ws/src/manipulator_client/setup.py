from setuptools import find_packages, setup
name="manipulator_client"
setup(name=name,version="0.1.0",packages=find_packages(),
data_files=[("share/ament_index/resource_index/packages",["resource/"+name]),("share/"+name,["package.xml"])],
install_requires=[],zip_safe=True,maintainer="RoboGame Team",maintainer_email="team@example.com",
description="Visual alignment and manipulator coordinator",license="MIT",
entry_points={"console_scripts":["manipulator_client = manipulator_client.node:main"]})

