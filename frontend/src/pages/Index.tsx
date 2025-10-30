import React, { useEffect, useState } from "react";
import { Layout, Steps, Button, Avatar, Dropdown, Space } from "antd";
import {
  UserOutlined,
  LogoutOutlined,
  DashboardOutlined,
} from "@ant-design/icons";
import type { MenuProps } from "antd";
import { useAuth } from "../contexts/AuthContext";
import LoginModal from "../components/LoginModal";
import StepContent from "../components/StepContent";
import logo from "../assets/ondc.png";
import {
  StyledHeader,
  LogoContainer,
  MainContent,
  ContentContainer,
  StepsContainer,
  LogoImage,
} from "../components/StyledComponents";
import { Link } from "react-router-dom";

const { Step } = Steps;

const Index: React.FC = () => {
  const [currentStep, setCurrentStep] = useState(0);
  const [loginModalOpen, setLoginModalOpen] = useState(false);
  const { isAuthenticated, logout, user } = useAuth();
  const [percent, setPercent] = useState<number>(10);

  const steps = [
    {
      title: "Instructions",
      description: "Read the guidelines",
    },
    {
      title: "Download Sample CSV",
      description: "Get the template file",
    },
    {
      title: "Upload CSV",
      description: "Submit your data",
    },
    {
      title: "Download CSV",
      description: "Generated file",
    },
  ];

  const userMenuItems: MenuProps["items"] = [
    {
      key: "profile",
      icon: <UserOutlined />,
      label: user?.name || "Profile",
    },
    {
      key: "dashboard",
      icon: <DashboardOutlined />,
      label: <Link to="/dashboard">Dashboard</Link>,
    },
    {
      key: "logout",
      icon: <LogoutOutlined />,
      label: "Logout",
      onClick: logout,
    },
  ];

  const handleStepClick = (step: number) => {
    if (step < steps.length - 1) setCurrentStep(step);
  };

  const handleSignInClick = () => {
    setLoginModalOpen(true);
  };

  const handleRegister = async () => {
    window.open(
      "https://forms.gle/yocysNhb7mkLFZDq5",
      "_blank",
      "noopener,noreferrer"
    );
  };
  return (
    <Layout style={{ minHeight: "100vh" }}>
      <StyledHeader>
        <LogoContainer>
          <div
            style={{
              width: "100px",
              height: "40px",
              marginLeft: "12px",
            }}
          >
            <LogoImage src={logo} alt="ONDC Logo" />
          </div>
        </LogoContainer>

        <div className="flex justify-center gap-4 items-center">
          {!isAuthenticated && (
            <Button
              type="primary"
              onClick={handleRegister}
              style={{
                background:
                  "linear-gradient(90deg, #1c75bc, #4aa1e0 51%, #1c75bc) var(--x, 100%) / 200%",
                color: "#fff",
              }}
            >
              Generate Token
            </Button>
          )}

          {isAuthenticated ? (
            <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
              <Space style={{ cursor: "pointer" }}>
                <Avatar
                  style={{ backgroundColor: "#fde3cf", color: "#f56a00" }}
                  icon={<UserOutlined />}
                ></Avatar>
                <span>{user?.name}</span>
              </Space>
            </Dropdown>
          ) : (
            <Button
              type="primary"
              onClick={handleSignInClick}
              style={{
                background:
                  "linear-gradient(90deg, #1c75bc, #4aa1e0 51%, #1c75bc) var(--x, 100%) / 200%",
                color: "#fff",
              }}
            >
              Sign In
            </Button>
          )}
        </div>
      </StyledHeader>

      <MainContent>
        <ContentContainer>
          <StepsContainer>
            <Steps
              current={currentStep}
              size="default"
              onChange={handleStepClick}
            >
              {steps.map((step, index) => (
                <Step
                  key={index}
                  title={step.title}
                  description={step.description}
                />
              ))}
            </Steps>

            <StepContent
              currentStep={currentStep}
              onSignInClick={handleSignInClick}
              setCurrentStep={setCurrentStep}
            />
          </StepsContainer>
        </ContentContainer>
      </MainContent>

      <LoginModal
        open={loginModalOpen}
        onCancel={() => setLoginModalOpen(false)}
      />
    </Layout>
  );
};

export default Index;
