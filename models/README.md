# Face models

Downloaded by `install.sh` from the [OpenCV model zoo](https://github.com/opencv/opencv_zoo)
(see `URLS`) and checked against `SHA256SUMS`:

- `face_detection_yunet_2023mar.onnx`: YuNet face detection, MIT License, by Shiqi Yu and contributors.
- `face_recognition_sface_2021dec.onnx`: SFace face recognition, Apache License 2.0.
- `auraface_glintr100.onnx`: AuraFace-v1 face recognition (`glintr100.onnx`,
  ResNet100 with ArcFace loss) by fal, Apache License 2.0, from
  [huggingface.co/fal/AuraFace-v1](https://huggingface.co/fal/AuraFace-v1),
  pinned to commit af6d057. Only the recognition model is used; the
  detection and landmark models in that repository (InsightFace weights,
  non-commercial) are not.
