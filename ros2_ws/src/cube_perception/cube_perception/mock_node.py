import rclpy
from rclpy.node import Node
from robogame_interfaces.msg import CubeDetection, CubeDetectionArray


class MockPerception(Node):
    def __init__(self) -> None:
        super().__init__("mock_perception")
        self.publisher = self.create_publisher(CubeDetectionArray, "/cubes", 10)
        self.create_timer(0.05, self._tick)

    def _tick(self) -> None:
        now = self.get_clock().now().to_msg()
        output = CubeDetectionArray(stamp=now)
        for color in (CubeDetection.ORANGE, CubeDetection.PURPLE):
            detection = CubeDetection()
            detection.stamp = now
            detection.color = color
            detection.confidence = 0.95
            detection.distance_m = 0.24
            detection.lateral_m = 0.0
            output.detections.append(detection)
        self.publisher.publish(output)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MockPerception()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()

