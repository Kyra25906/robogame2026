from setuptools import find_packages, setup

name = "robot_bridge"
setup(name=name, version="0.1.0", packages=find_packages(),
      data_files=[("share/ament_index/resource_index/packages", ["resource/" + name]),
                  ("share/" + name, ["package.xml"])],
      install_requires=["pyserial"], zip_safe=True,
      maintainer="RoboGame Team", maintainer_email="team@example.com",
      description="USB serial bridge and mock hardware", license="MIT",
      entry_points={"console_scripts": ["robot_bridge = robot_bridge.node:main"]})

