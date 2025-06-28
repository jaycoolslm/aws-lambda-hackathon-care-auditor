cd .venv/lib/python3.11/site-packages
zip -r ../../../../my_deployment_package.zip .
cd ../../../../
zip my_deployment_package.zip lambda_function.py