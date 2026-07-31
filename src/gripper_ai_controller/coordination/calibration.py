"""标定数据管理"""
import json
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime
import numpy as np

from .transformer import Transform3D, CoordinateTransformer


class CalibrationManager:
    """
    标定数据管理器

    负责存储、加载和验证相机标定和手眼标定数据
    """

    def __init__(self, storage_dir: Path):
        """
        初始化标定管理器

        Args:
            storage_dir: 标定数据存储目录
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.camera_intrinsics_file = self.storage_dir / "camera_intrinsics.json"
        self.hand_eye_file = self.storage_dir / "hand_eye_calibration.json"
        self.calibration_samples_dir = self.storage_dir / "samples"
        self.calibration_samples_dir.mkdir(exist_ok=True)

    def save_camera_intrinsics(
        self,
        camera_matrix: np.ndarray,
        distortion_coeffs: np.ndarray,
        image_size: tuple,
        reprojection_error: float,
        num_images: int,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        保存相机内参标定结果

        Args:
            camera_matrix: 3x3 内参矩阵
            distortion_coeffs: 畸变系数
            image_size: 图像尺寸 (width, height)
            reprojection_error: 重投影误差(像素)
            num_images: 标定使用的图像数量
            metadata: 额外的元数据
        """
        data = {
            "calibration_type": "camera_intrinsics",
            "timestamp": datetime.now().isoformat(),
            "camera_matrix": camera_matrix.tolist(),
            "distortion_coeffs": distortion_coeffs.tolist(),
            "image_size": list(image_size),
            "reprojection_error": float(reprojection_error),
            "num_images": num_images,
            "metadata": metadata or {}
        }

        with open(self.camera_intrinsics_file, 'w') as f:
            json.dump(data, f, indent=2)

    def load_camera_intrinsics(self) -> Optional[Dict[str, Any]]:
        """
        加载相机内参

        Returns:
            标定数据字典，如果文件不存在返回 None
        """
        if not self.camera_intrinsics_file.exists():
            return None

        with open(self.camera_intrinsics_file, 'r') as f:
            data = json.load(f)

        # 转换为numpy数组
        data["camera_matrix"] = np.array(data["camera_matrix"])
        data["distortion_coeffs"] = np.array(data["distortion_coeffs"])
        data["image_size"] = tuple(data["image_size"])

        return data

    def save_hand_eye_calibration(
        self,
        camera_to_base_rotation: np.ndarray,
        camera_to_base_translation: np.ndarray,
        calibration_error: float,
        num_samples: int,
        sample_poses: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        保存手眼标定结果

        Args:
            camera_to_base_rotation: 3x3 旋转矩阵
            camera_to_base_translation: 3x1 平移向量
            calibration_error: 标定误差(mm)
            num_samples: 标定样本数量
            sample_poses: 标定时使用的机器人位姿列表
            metadata: 额外的元数据
        """
        data = {
            "calibration_type": "hand_eye",
            "timestamp": datetime.now().isoformat(),
            "camera_to_base": {
                "rotation": camera_to_base_rotation.tolist(),
                "translation": camera_to_base_translation.tolist()
            },
            "calibration_error": float(calibration_error),
            "num_samples": num_samples,
            "sample_poses": sample_poses or [],
            "metadata": metadata or {}
        }

        with open(self.hand_eye_file, 'w') as f:
            json.dump(data, f, indent=2)

    def load_hand_eye_calibration(self) -> Optional[Dict[str, Any]]:
        """
        加载手眼标定数据

        Returns:
            标定数据字典，如果文件不存在返回 None
        """
        if not self.hand_eye_file.exists():
            return None

        with open(self.hand_eye_file, 'r') as f:
            data = json.load(f)

        # 转换为numpy数组
        data["camera_to_base"]["rotation"] = np.array(
            data["camera_to_base"]["rotation"]
        )
        data["camera_to_base"]["translation"] = np.array(
            data["camera_to_base"]["translation"]
        )

        return data

    def create_transformer(self) -> Optional[CoordinateTransformer]:
        """
        从已保存的标定数据创建坐标转换器

        Returns:
            CoordinateTransformer 实例，如果标定数据不完整返回 None
        """
        intrinsics = self.load_camera_intrinsics()
        hand_eye = self.load_hand_eye_calibration()

        if intrinsics is None or hand_eye is None:
            return None

        transform = Transform3D(
            rotation=hand_eye["camera_to_base"]["rotation"],
            translation=hand_eye["camera_to_base"]["translation"]
        )

        return CoordinateTransformer(
            camera_matrix=intrinsics["camera_matrix"],
            distortion_coeffs=intrinsics["distortion_coeffs"],
            camera_to_base_transform=transform,
            image_size=intrinsics["image_size"]
        )

    def is_calibrated(self) -> Dict[str, bool]:
        """
        检查标定状态

        Returns:
            包含各标定状态的字典
        """
        return {
            "camera_intrinsics": self.camera_intrinsics_file.exists(),
            "hand_eye": self.hand_eye_file.exists(),
            "fully_calibrated": (
                self.camera_intrinsics_file.exists() and
                self.hand_eye_file.exists()
            )
        }

    def get_calibration_summary(self) -> Dict[str, Any]:
        """
        获取标定摘要信息

        Returns:
            标定状态和参数摘要
        """
        status = self.is_calibrated()
        summary = {"status": status}

        if status["camera_intrinsics"]:
            intrinsics = self.load_camera_intrinsics()
            summary["camera_intrinsics"] = {
                "timestamp": intrinsics["timestamp"],
                "reprojection_error": intrinsics["reprojection_error"],
                "num_images": intrinsics["num_images"],
                "image_size": intrinsics["image_size"],
                "focal_length": {
                    "fx": float(intrinsics["camera_matrix"][0, 0]),
                    "fy": float(intrinsics["camera_matrix"][1, 1])
                }
            }

        if status["hand_eye"]:
            hand_eye = self.load_hand_eye_calibration()
            summary["hand_eye"] = {
                "timestamp": hand_eye["timestamp"],
                "calibration_error": hand_eye["calibration_error"],
                "num_samples": hand_eye["num_samples"]
            }

        return summary

    def save_calibration_sample(
        self,
        sample_id: int,
        image_path: str,
        robot_pose: Dict[str, float],
        board_pose_in_camera: Optional[Dict[str, Any]] = None
    ) -> Path:
        """
        保存一个标定样本数据

        Args:
            sample_id: 样本编号
            image_path: 标定图像路径
            robot_pose: 机器人TCP位姿 {x, y, z, rx, ry, rz}
            board_pose_in_camera: 标定板在相机中的位姿

        Returns:
            样本数据文件路径
        """
        sample_data = {
            "sample_id": sample_id,
            "timestamp": datetime.now().isoformat(),
            "image_path": str(image_path),
            "robot_pose": robot_pose,
            "board_pose_in_camera": board_pose_in_camera
        }

        sample_file = self.calibration_samples_dir / f"sample_{sample_id:03d}.json"
        with open(sample_file, 'w') as f:
            json.dump(sample_data, f, indent=2)

        return sample_file

    def load_calibration_samples(self) -> List[Dict[str, Any]]:
        """
        加载所有标定样本

        Returns:
            样本数据列表
        """
        samples = []
        for sample_file in sorted(self.calibration_samples_dir.glob("sample_*.json")):
            with open(sample_file, 'r') as f:
                samples.append(json.load(f))
        return samples

    def clear_samples(self) -> None:
        """清除所有标定样本"""
        for sample_file in self.calibration_samples_dir.glob("sample_*.json"):
            sample_file.unlink()

    def export_config(self, output_path: Path) -> None:
        """
        导出完整标定配置（用于备份或迁移）

        Args:
            output_path: 输出文件路径
        """
        config = {
            "exported_at": datetime.now().isoformat(),
            "camera_intrinsics": self.load_camera_intrinsics(),
            "hand_eye": self.load_hand_eye_calibration(),
            "status": self.is_calibrated()
        }

        with open(output_path, 'w') as f:
            json.dump(config, f, indent=2, default=str)

    def import_config(self, config_path: Path) -> None:
        """
        导入标定配置

        Args:
            config_path: 配置文件路径
        """
        with open(config_path, 'r') as f:
            config = json.load(f)

        if config.get("camera_intrinsics"):
            intrinsics = config["camera_intrinsics"]
            self.save_camera_intrinsics(
                camera_matrix=np.array(intrinsics["camera_matrix"]),
                distortion_coeffs=np.array(intrinsics["distortion_coeffs"]),
                image_size=tuple(intrinsics["image_size"]),
                reprojection_error=intrinsics["reprojection_error"],
                num_images=intrinsics["num_images"],
                metadata=intrinsics.get("metadata")
            )

        if config.get("hand_eye"):
            hand_eye = config["hand_eye"]
            self.save_hand_eye_calibration(
                camera_to_base_rotation=np.array(
                    hand_eye["camera_to_base"]["rotation"]
                ),
                camera_to_base_translation=np.array(
                    hand_eye["camera_to_base"]["translation"]
                ),
                calibration_error=hand_eye["calibration_error"],
                num_samples=hand_eye["num_samples"],
                sample_poses=hand_eye.get("sample_poses"),
                metadata=hand_eye.get("metadata")
            )
