"""Hello, dog. Walk forward, turn, stop.

    python  examples/01_hello_dog.py      (Ubuntu)
    mjpython examples/01_hello_dog.py     (macOS -- GUI must own the main thread)
"""

from go2 import Go2

with Go2(sim=True) as dog:
    print("starting at", dog.pose())

    dog.stand()
    dog.wait(1)

    print("walking forward...")
    dog.move(vx=0.4)
    dog.wait(3)

    print("turning left...")
    dog.move(wz=0.8)
    dog.wait(2)

    print("forward again...")
    dog.move(vx=0.4)
    dog.wait(3)

    dog.stop()
    dog.wait(1)
    print("finished at", dog.pose())
