#export AWS_ACCESS_KEY_ID=xxxxxx
#export AWS_SECRET_ACCESS_KEY=zxxxxxxxxxxx
#export AWS_DEFAULT_REGION=xxxxxxx
#export ACCOUNT_ID=zxxxxxxxxxxx
export REPO_NAME=vibeconnect-insights-ai
export IMAGE_NAME=insightsai
export IMAGE_TAG=$BITRISE_TRIGGERED_WORKFLOW_ID-$BITRISE_BUILD_NUMBER
export ECR_URL=$ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com

docker build -f Dockerfile \
  --no-cache \
  --progress=plain \
  --build-arg OPENAI_API_KEY=$OPENAI_API_KEY \
  --build-arg OPENAI_MODEL=$OPENAI_MODEL \
  --build-arg GOOGLE_PLACES_API_KEY=$GOOGLE_PLACES_API_KEY \
  --provenance=false \
  -t ${IMAGE_NAME}:${IMAGE_TAG} .

docker run --rm -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION amazon/aws-cli \
  ecr get-login-password --region $AWS_DEFAULT_REGION | \
  docker login --username AWS --password-stdin $ECR_URL

docker tag $IMAGE_NAME:$IMAGE_TAG $ECR_URL/$REPO_NAME:$IMAGE_TAG

docker push $ECR_URL/$REPO_NAME:$IMAGE_TAG