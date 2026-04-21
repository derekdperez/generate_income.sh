cd ~/nightmare
sudo docker-compose -f deploy/docker-compose.central.yml --env-file deploy/.env down -v
rm -f deploy/.env deploy/worker.env.generated deploy/coordinator-host-env.sh
rm -f deploy/tls/server.crt deploy/tls/server.key
sudo ./full_deploy_command.sh
