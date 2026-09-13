#!/bin/bash
# =============================================================================
# AWS ML Cost Optimizer — Setup Script
# Déploie l'infrastructure complète en une commande
# Usage: bash setup.sh
# =============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo ""
echo -e "${BLUE}=================================================${NC}"
echo -e "${BLUE}   AWS ML Cost Optimizer — Setup${NC}"
echo -e "${BLUE}=================================================${NC}"
echo ""

# ── 1. Vérifier les prérequis ─────────────────────────────────────────────────

echo -e "${YELLOW}Checking prerequisites...${NC}"

if ! command -v aws &> /dev/null; then
    echo -e "${RED}❌ AWS CLI not found. Install it: https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html${NC}"
    exit 1
fi

if ! command -v terraform &> /dev/null; then
    echo -e "${RED}❌ Terraform not found. Install it: https://developer.hashicorp.com/terraform/install${NC}"
    exit 1
fi

if ! command -v python3 &> /dev/null && ! command -v python &> /dev/null; then
    echo -e "${RED}❌ Python not found. Install Python 3.12+${NC}"
    exit 1
fi

# Vérifier les credentials AWS
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}❌ AWS credentials not configured. Run: aws configure${NC}"
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region || echo "eu-west-1")
echo -e "${GREEN}✅ AWS Account: ${ACCOUNT_ID} | Region: ${REGION}${NC}"

# ── 2. Demander l'email ───────────────────────────────────────────────────────

echo ""
echo -e "${YELLOW}Enter your email address to receive weekly reports:${NC}"
read -r NOTIFICATION_EMAIL

if [[ ! "$NOTIFICATION_EMAIL" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
    echo -e "${RED}❌ Invalid email address${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Reports will be sent to: ${NOTIFICATION_EMAIL}${NC}"

# ── 3. Bootstrap remote state ─────────────────────────────────────────────────

echo ""
echo -e "${YELLOW}Setting up Terraform remote state...${NC}"

BUCKET_NAME="ml-cost-optimizer-tfstate-${ACCOUNT_ID}"
TABLE_NAME="ml-cost-optimizer-tflock"

# Créer le bucket S3 si inexistant
if aws s3 ls "s3://${BUCKET_NAME}" &> /dev/null; then
    echo -e "${GREEN}✅ S3 state bucket already exists${NC}"
else
    if [ "$REGION" = "us-east-1" ]; then
        aws s3 mb "s3://${BUCKET_NAME}" --region "$REGION" > /dev/null
    else
        aws s3 mb "s3://${BUCKET_NAME}" --region "$REGION" \
            --create-bucket-configuration LocationConstraint="$REGION" > /dev/null
    fi
    aws s3api put-bucket-versioning \
        --bucket "$BUCKET_NAME" \
        --versioning-configuration Status=Enabled > /dev/null
    echo -e "${GREEN}✅ S3 state bucket created: ${BUCKET_NAME}${NC}"
fi

# Créer la table DynamoDB si inexistante
if aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$REGION" &> /dev/null; then
    echo -e "${GREEN}✅ DynamoDB lock table already exists${NC}"
else
    aws dynamodb create-table \
        --table-name "$TABLE_NAME" \
        --attribute-definitions AttributeName=LockID,AttributeType=S \
        --key-schema AttributeName=LockID,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        --region "$REGION" > /dev/null
    echo -e "${GREEN}✅ DynamoDB lock table created${NC}"
fi

# ── 4. Mettre à jour le backend Terraform avec le bon bucket ──────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERRAFORM_DIR="${SCRIPT_DIR}/terraform"

# Remplacer le nom du bucket dans main.tf
sed -i.bak \
    "s|bucket.*=.*\"ml-cost-optimizer-tfstate\"|bucket         = \"${BUCKET_NAME}\"|g" \
    "${TERRAFORM_DIR}/main.tf" && rm -f "${TERRAFORM_DIR}/main.tf.bak"

# ── 5. Build Lambda zip ───────────────────────────────────────────────────────

echo ""
echo -e "${YELLOW}Building Lambda package...${NC}"

LAMBDA_DIR="${SCRIPT_DIR}/lambda"
cd "$LAMBDA_DIR"

# Installer les dépendances dans un dossier temporaire
rm -rf package/
pip install -r requirements.txt -t package/ -q
cp guardrail.py package/
cd package && zip -r ../function.zip . -x "*.pyc" -x "*/__pycache__/*" > /dev/null
cd ..
rm -rf package/

echo -e "${GREEN}✅ Lambda package built ($(du -sh function.zip | cut -f1))${NC}"

# ── 6. Terraform init + apply ─────────────────────────────────────────────────

echo ""
echo -e "${YELLOW}Deploying infrastructure with Terraform...${NC}"

cd "$TERRAFORM_DIR"

terraform init -reconfigure \
    -backend-config="bucket=${BUCKET_NAME}" \
    -backend-config="region=${REGION}" \
    -input=false > /dev/null

terraform apply \
    -var="notification_email=${NOTIFICATION_EMAIL}" \
    -var="aws_region=${REGION}" \
    -auto-approve \
    -input=false

# ── 7. Confirmer l'abonnement SNS ─────────────────────────────────────────────

echo ""
echo -e "${GREEN}=================================================${NC}"
echo -e "${GREEN}   ✅ Deployment complete!${NC}"
echo -e "${GREEN}=================================================${NC}"
echo ""
echo -e "${YELLOW}⚠️  IMPORTANT — Check your inbox now:${NC}"
echo -e "   AWS sent a confirmation email to ${NOTIFICATION_EMAIL}"
echo -e "   You MUST click 'Confirm subscription' to receive reports."
echo ""
echo -e "${BLUE}What happens next:${NC}"
echo -e "   📅 Every Monday at 8:00 AM UTC, you'll receive a cost report"
echo -e "   📊 Reports are also saved to S3 for audit history"
echo -e "   💰 Infrastructure cost: under \$1/month"
echo ""
echo -e "${BLUE}To run a manual scan right now:${NC}"
FUNCTION_NAME=$(terraform output -raw lambda_function_name 2>/dev/null || echo "ml-cost-optimizer-analyzer")
echo -e "   aws lambda invoke --function-name ${FUNCTION_NAME} \\"
echo -e "     --region ${REGION} --payload '{}' \\"
echo -e "     --cli-binary-format raw-in-base64-out /tmp/response.json"
echo -e "   cat /tmp/response.json"
echo ""
echo -e "${BLUE}To destroy all resources:${NC}"
echo -e "   cd terraform && terraform destroy -var=\"notification_email=${NOTIFICATION_EMAIL}\""
echo ""
