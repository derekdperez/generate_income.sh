cd deploy
docker-compose -f docker-compose.central.yml ps
docker-compose -f docker-compose.central.yml logs --tail=100 postgres
docker-compose -f docker-compose.central.yml logs --tail=100 server

docker-compose -f docker-compose.central.yml exec postgres psql -U nightmare -d nightmare -c "\dt"

aws ec2 describe-instances --filters "Name=instance-state-name,Values=running" --query 'Reservations[].Instances[].{Id:InstanceId,Name:Tags[?Key==`Name`]|[0].Value,State:State.Name,PublicIP:PublicIpAddress}' --output table