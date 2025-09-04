# RAVEN User Interface for LOGOS and DACKAR

This project contains a visualization app for the LOGOS and DACKAR RAVEN apps. The interface is a web-based application with
postgreSQL backend for database management. For compability, the application utilizes a docker container that wraps the postgreSQL and Streamlit together.
 
# Quick Start

1. Clone the project directory into your computer system with all associated files
2. Download and install the Docker application. Detailed instructions below.
3. (optional) Download and install Anaconda and run the environment DACKAR_v0. Detailed instructions below.
4. Navigate to the folder directory in an Anaconda terminal with the DACKAR_v0 environment or using powershell or a command line terminal of your choice. Use ```cd``` to change directory and ```dir``` or ```ls``` to show current directory contents 
5. Navigate to ```Move_Me``` folder. If you are using an INL computer, click INL, otherwise click Public. Copy the file ```Dockerfile``` into the ```UI_src``` folder. This folder is needed to build the container. 
	- If you are using an INL computer, you will also need to download a certificate from the INL website. Contact Edward Chen for additional assistance on certificate. 
6. Run the application using the following commands:
```
docker-compose up --build
```
7. After the docker-compose finishes in the termical, open up a new web browser and type ```http://localhost:8501/```
8. The app should appear with a menu bar on the side. The server connection is successfully if on the side bar, a green box notification pops up.
9. To close the app completely, in the command shell enter ```docker-compose down```. This will remove all data and volumes associated with the containter.

The app interface should look like:
![app_interface](https://github.com/idaholab/LOGOS/blob/chenE/BaseUI/UI_src/README_assets/App_interface.png)

# Requirements/Software
- Docker
- Conda (optional)
- Streamlit (optional)
- PostgreSQL (optional)

## Info related to Docker (User & Developer Required)
Docker desktop is the preferred application to run the application during development. The docker image and build files are included in this repo; however the docker application is required to run them. 

### Windows Installation
This is the original operating system that the container was developed on. Compatibility with other operating systems cannot currently be guaranteed. 

1. Go to https://docs.docker.com/desktop/setup/install/windows-install/
2. Select "Docker Desktop for Windows - x86_64"
3. Follow the steps in the Docker installation wizard.
    - After installation, the Docker application will ask if you have WSL turned on. For Docker to operate properly, you will need to turn these features on.
5. In the search bar of your windows machine, type "Windows Features". An option should appear that says "Turn Windows features on or off". Click this option.
6. Check that the following boxes are ticked: "Windows Subsystem for Linux", "Virtual Machine Platform", and "Windows Hypervisor Platform".
7. After these are ticked and OK is pressed, the necessary files will be updated/installed and a restart will be needed.
8. After restarting, to check if WSL is correctly implemented, open a powershell terminal and type ```wsl --version```
9. If WSL is missing, use the command ```wsl --install``` to install WSL
10. Check that the latest WSL is installed by running the command ```wsl --update```

## Info Related to Anaconda (Development Environment)
Anaconda is the preferred development environment to modify the app and all dependencies

### Installation Instructions (Developer ONLY)
1. The installation for Anaconda is typical with no special considerations. Follow the steps found in https://www.anaconda.com/docs/getting-started/anaconda/install#windows-installation to download and install. 
2. After installation, open an Anaconda prompt terminal. The terminal should look like this:
![conda](https://github.com/idaholab/LOGOS/blob/chenE/BaseUI/UI_src/README_assets/conda.png)
3. Navigate to the directory where the source files are located. It should be ```/UI_scr/```. You should see the file ```DACKAR_v0.yml```. This is an environment file that will be needed for first time users
4. If this is your first time running Anaconda, you will need to install the corresponding environment. In the anaconda environment (after navigating to the folder), type:
```conda env create -f DACKAR_v0.yml```
6. Follow the Anaconda instructions to install the environment.
7. An environment in conda should have been installed. You can activate the environment by typing:
```conda activate DACKAR_v0```
8. You will know if the conda environment is activated if the prompt line changes from (base) to (DACKAR_v0). You can check that the correct files are installed by using ```conda list```.
   
## Info Related to PostgreSQL (Database Management)
Installation of PostgreSQL is only necessary if you are an app developer. This is separate from the anaconda installation pathway and is **necessary**. Otherwise, these steps can be bypassed. If you are a user, the docker-compose file will automatically setup the postgreSQL database for you within a container. 
PostgreSQL is a database management application that enables persistent data logging and retrieval. It uses SQL langauge to conduct these prompots.

### Installation Instructions (Developer ONLY)
These instructions apply to a windows installation. Other operating systems are supported but the instructions may differ. 

1. Navigate to https://www.postgresql.org/download/ and select the operating system you are using.
2. For windows, the installer is located on EDB: Open-Source site. There is a link in the postgresql site. Download the installer and run.
3. If you are an INL employee, you may need additional permission to download the software. Contact your manager for approval then call the INL IT hotline for the response code.
4. Once the installer executing, click next until it asks for LOCALE. Do not select DEFAULT as the server will fail to start. Choose ```en-US``` or equivalent. 
5. Click next until you see a screen that asks for a password.

![postgresql_install](https://github.com/idaholab/LOGOS/blob/chenE/BaseUI/UI_src/README_assets/postgres_installation.png)   

7. **IMPORTANT** The username is defaulted to **postgres**. Enter your custom password at this time. Remember this password as it cannot be changed later and there is **NO password recovery option**.
8. Choose **port 5432** when asked. Leave all other options default. 
9. No further additional installation options are required. You will know if it successfully installed if in the search bar of windows, there exists the pgAdmin4 app.
10. Open the pgAdmin4 app. 
11. On the dashboard, click "Add New Server" under Quick Links
12. Enter the parameters in the following fields:
  - Name = RAVEN_Application_DB
  - Host name/address = localhost
  - Port = 5432
  - Maintenance database = postgres
  - Username = postgres
  - Password = "your password"
    - Replace "your password" with the password you chose during setup.
12. Click save. The server should be successfully setup and a new database should pop up under Servers

![postgresql_server](https://github.com/idaholab/LOGOS/blob/chenE/BaseUI/UI_src/README_assets/postgres_server.png)

That's it. Your server is running in the background. Whenever you need to connect to the server, you will need to open pgAdmin4. If you are trying to connect to the server via streamlit, you will need it open. 

## Info Related to Streamlit (Application Development Software)
Installation of Streamlit is only necessary if you are an app developer. Furthermore, if you DID NOT use the anaconda installation pathway, you will need to install Streamlit. Otherwise, these steps can be bypassed. Streamlit is the python application used to develop the web app. Streamlit does not need to be installed if only the container is to be run. This is because the docker compose file contain instructions to automatically install and setup Streamlit. 

### Installation (Developer ONLY, OPTIONAL)


## Info Related to INL Computers
Installation on INL computers can be complex as certificate issues and block downloads. If you are encountering an SSL certificate issue, you will need to do two things. First, ensure that you have a copy of the certificate in the ```UI_src``` directory. It should have a ```.crt``` file format. Second, ensure that you use the Dockerfile under ```\Move_me\INL\Dockerfile```. The public dockerfile will NOT work. 
