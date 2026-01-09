A personal initiative to design and implement a structured versioning and repository strategy for PLC programs hosted on GitHub

Set up a full DEV test environment using a software PLC with CODESYS Virtual Control SL running in Docker, made persistence and networking solid, and validated the workflow end to end.

DEV PLC is running as CODESYS Virtual Control SL inside Docker on Server 1.
 The Engineering PC runs as a VM on Server 2 using Proxmox.
 Both are on the same network, connected over Ethernet, keeping DEV clean, isolated, and reproducible.
 <img width="800" height="535" alt="image" src="https://github.com/user-attachments/assets/4d082c2c-3e55-44a0-b8f2-8bf2e6ce4c30" />


Built a headless CODESYS script that can connect to a running DEV PLC, pull the source from the controller, export a project archive, and run fully unattended with proper logging.
<img width="1970" height="1316" alt="image" src="https://github.com/user-attachments/assets/60d8cbac-586a-4b2a-8691-ea02aa75c71b" />


The PLC stays the source of truth. A scheduled process runs a headless script on the engineering PC, pulls the source from a running DEV PLC, exports it as PLCopen XML, normalizes it to remove all the noisy metadata, and only commits when something meaningfully changes. If there’s no real diff, nothing gets pushed.
<img width="2030" height="1353" alt="image" src="https://github.com/user-attachments/assets/c083a2e0-5c72-446f-a819-b0088c37ded8" />
