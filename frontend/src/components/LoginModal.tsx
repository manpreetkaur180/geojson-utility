import React, { useEffect, useState } from "react";
import { Modal, Form, Input, Button, message } from "antd";
import { LockOutlined } from "@ant-design/icons";
import axios from "axios";
import { useAuth } from "../contexts/AuthContext";
import { LoginCredentials } from "../types/auth";

interface LoginModalProps {
  open: boolean;
  onCancel: () => void;
}

const LoginModal: React.FC<LoginModalProps> = ({ open, onCancel }) => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const apiUrl = import.meta.env.VITE_API_BASE_URL;
  const handleLogin = async (values: LoginCredentials) => {
    setLoading(true);
    localStorage.setItem("jwt_token", values.token);
    try {
      const response = await axios.post(
        `${apiUrl}/auth/login`,
        { token: values.token },
        {
          headers: { "Content-Type": "application/json" },
        }
      );
      if (response) {
        message.success("Login successful!");

        localStorage.setItem("user_data", response?.data?.username);
        const token = values.token;
        const user = response?.data?.username;
        login(token, user);

        onCancel();
        form.resetFields();
        setLoading(false);
        window.location.reload();
      }
    } catch (err: any) {
      message.error(err.message || "Invalid credentials. Please try again.");
      setLoading(false);
    }
  };
  const handleRegister = async () => {
    window.open(
      "https://forms.gle/yocysNhb7mkLFZDq5",
      "_blank",
      "noopener,noreferrer"
    );
  };

  return (
    <Modal
      title="Sign In"
      open={open}
      onCancel={onCancel}
      footer={null}
      centered
    >
      <Form
        form={form}
        name="login"
        onFinish={handleLogin}
        autoComplete="off"
        layout="vertical"
      >
        <Form.Item
          label="Token"
          name="token"
          rules={[{ required: true, message: "Please enter your token!" }]}
        >
          <Input
            prefix={<LockOutlined />}
            placeholder="Enter your token here"
            size="large"
          />
        </Form.Item>
        <div className="mb-2">
          <span
            style={{
              textAlign: "center",
              marginTop: "16px",
              fontSize: "14px",
              fontWeight: 600,
              color: "#666",
              cursor: "pointer",
            }}
          >
            Note :
          </span>
          <span
            style={{
              textAlign: "center",
              marginTop: "16px",
              fontSize: "12px",
              color: "#666",
            }}
          >
            {" "}
            kindly note that the token has been shared with you through email.{" "}
          </span>
        </div>

        <Form.Item>
          <Button
            style={{
              background:
                "linear-gradient(90deg, #1c75bc, #4aa1e0 51%, #1c75bc) var(--x, 100%) / 200%",
              color: "#fff",
            }}
            type="primary"
            htmlType="submit"
            loading={loading}
            size="large"
            block
          >
            Sign In
          </Button>
        </Form.Item>
      </Form>

      <div
        style={{
          textAlign: "center",
          marginTop: "16px",
          fontSize: "12px",
          color: "#666",
        }}
      >
        don't have credential to login?{" "}
        <span
          style={{
            textAlign: "center",
            marginTop: "16px",
            fontSize: "14px",
            fontWeight: 600,
            color: "#666",
            cursor: "pointer",
          }}
          onClick={handleRegister}
        >
          Register here
        </span>
      </div>
    </Modal>
  );
};

export default LoginModal;
