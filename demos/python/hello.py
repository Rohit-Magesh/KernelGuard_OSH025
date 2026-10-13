import os
print("Hello from Python in the sandbox")
print("uid =", os.getuid(), "(non-root)" if os.getuid() != 0 else "(ROOT!)")
try:
    open("/etc/shadow").read()
except Exception as e:
    print("access control working:", e)
