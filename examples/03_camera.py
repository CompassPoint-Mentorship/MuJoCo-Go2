"""Grab what the dog sees and save it to a PNG.

Runs headless -- no window needed. On a machine with no display, set
MUJOCO_GL=egl (GPU) or MUJOCO_GL=osmesa (software) first.
"""

from go2 import Go2

try:
    from PIL import Image
except ImportError:
    raise SystemExit("This example needs Pillow:  pip install pillow")

with Go2(sim=True, viewer=False) as dog:
    dog.stand()
    dog.move(vx=0.5)
    dog.wait(3)          # walk toward the obstacles
    dog.stop()

    image = dog.camera(width=640, height=480)
    print("image:", image.shape, image.dtype)
    Image.fromarray(image).save("dog_view.png")
    print("wrote dog_view.png  (pose:", dog.pose(), ")")
