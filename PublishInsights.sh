#export AWS_ACCESS_KEY_ID=xxxxx
#export AWS_SECRET_ACCESS_KEY=xxxxx
#export AWS_DEFAULT_REGION=xxxxx
#export ACCOUNT_ID=xxxxxx
export REPO_NAME=vibeconnect
export IMAGE_NAME=server-vibe-connect-swift
export IMAGE_TAG=$BITRISE_TRIGGERED_WORKFLOW_ID-$BITRISE_BUILD_NUMBER
export ECR_URL=$ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com

# Construir l'image
docker build -f Dockerfile \
  -t ${IMAGE_NAME}:${IMAGE_TAG} .

# Authentification à ECR
docker run --rm -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION amazon/aws-cli \
  ecr get-login-password --region $AWS_DEFAULT_REGION | \
  docker login --username AWS --password-stdin $ECR_URL

# Tague l'image
docker tag $IMAGE_NAME:$IMAGE_TAG $ECR_URL/$REPO_NAME:$IMAGE_TAG

# Pousse l'image
docker push $ECR_URL/$REPO_NAME:$IMAGE_TAG
