import * as EventSource from "eventsource";
import { useEffect, useState } from "react";
import { Button, Upload, Progress, message, Typography, Spin } from "antd";
import {
  DownloadOutlined,
  UploadOutlined,
  FileTextOutlined,
} from "@ant-design/icons";
import type { UploadFile, UploadProps } from "antd";
import { useAuth } from "../contexts/AuthContext";
import {
  StepCard,
  InstructionList,
  InstructionListItem,
  UploadSection,
} from "./StyledComponents";
import axios from "axios";

const { Title, Paragraph } = Typography;

interface StepContentProps {
  currentStep: number;
  onSignInClick: () => void;
  setCurrentStep: React.Dispatch<React.SetStateAction<number>>;
}

const StepContent: React.FC<StepContentProps> = ({
  currentStep,
  onSignInClick,
  setCurrentStep,
}) => {
  const { isAuthenticated, token, logout, user } = useAuth();
  const [uploadProgress, setUploadProgress] = useState(0);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [next, setNext] = useState(false);
  const [result, setResult] = useState("");
  const [id, setId] = useState(null);
  const [file, setFile] = useState<File | null>(null);

  async function hashToken(token: string): Promise<string> {
    const encoder = new TextEncoder();
    const data = encoder.encode(token);
    const hashBuffer = await crypto.subtle.digest("SHA-256", data);
    const hashArray = Array.from(new Uint8Array(hashBuffer)); // convert buffer to byte array
    const hashHex = hashArray
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");

    return hashHex;
  }
  const apiUrl = import.meta.env.VITE_API_BASE_URL;
  useEffect(() => {
    setNext(false);
    setUploading(false);
    setFileList([]);
  }, []);

  useEffect(() => {
    if (!token || !id) return;
    const setupEventSource = async () => {
      const hashedToken = await hashToken(token);

      if (id) {
        const eventSource = new EventSource.EventSource(
          `${apiUrl}/catchment/csv-status-stream/${id}?hashed_token=${hashedToken}&username=${user.name}`
        );

        eventSource.onmessage = (event) => {
          const data = JSON.parse(event.data);
          if (data.type === "complete") {
            if (data.status === "done" || data.status === "partial") {
              setResult("done");
              message.success({
                content: "CSV processed successfully!",
                duration: 2,
              });
            } else {
              setResult("failed");
              message.error({
                content: data.error || "Something went wrong!",
                duration: 4,
              });
            }
            eventSource.close();
          }
          if (data.type === "init") {
            if (data.status === "processing") {
              setResult("pending");
            }
          }
        };

        eventSource.onerror = () => {
          message.error({
            content: "Facing some issue while fetching file status",
            duration: 4,
          });
          setResult("fail");
          eventSource.close();
        };

        return () => {
          eventSource.close();
        };
      }
    };
    setupEventSource();
  }, [id, apiUrl]);

  const onNextClick = () => {
    if (file) {
      handleUpload(file);
    }
  };
  const handleDownloadCSV = () => {
    const link = document.createElement("a");
    link.href = "/sample.csv";
    link.setAttribute("download", "sample.csv");
    document.body.appendChild(link);
    link.click();
    link.remove();

    message.success({
      content: "CSV file downloaded successfully!",
      duration: 2,
    });
  };

  const onDownloadSCV = async () => {
    try {
      const response = await axios.get(
        `${apiUrl}/catchment/csv/${id}`,

        {
          headers: {
            responseType: "blob",
            Authorization: `Bearer ${token}`,
          },
        }
      );

      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", "sample.csv");
      document.body.appendChild(link);
      link.click();
      link.remove();

      message.success({
        content: "CSV file downloaded successfully!",
        duration: 2,
      });
    } catch (error) {
      if (error.response && error.response.status === 401) {
        message.error({
          content: "Session expired. Please log in again.",
          duration: 4,
        });
        logout();
      } else {
        message.error({
          content: "Download failed",
          duration: 4,
        });
      }
    }
  };

  const handleUpload = async (file: File) => {
    setUploading(true);
    setUploadProgress(0);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const response = await axios.post(`${apiUrl}/catchment/bulk`, formData, {
        headers: {
          "Content-Type": "multipart/form-data",
          Authorization: `Bearer ${token}`,
        },
        onUploadProgress: (progressEvent) => {
          const percent = Math.round(
            (progressEvent.loaded * 100) / (progressEvent.total || 1)
          );
          setUploadProgress(percent);
        },
      });

      if (response) {
        message.success({
          content: "File uploaded successfully!",
          duration: 2,
        });
        const id = response?.data?.csv_id;
        setId(id);
        setResult("pending");
        setCurrentStep(3);
      }
    } catch (error: any) {
      setUploadProgress(0);
      if (error.response?.status === 401) {
        message.error({
          content: "Session expired. Please log in again.",
          duration: 4,
        });
        logout();
      } else {
        message.error({
          content: error?.response?.data?.detail,
          duration: 8,
        });
      }
    } finally {
      setUploading(false);
    }
  };

  const uploadProps: UploadProps = {
    name: "file",
    accept: ".csv",
    fileList,
    beforeUpload: async (file) => {
      if (!isAuthenticated) {
        message.error({
          content: "Please sign in to upload files",
          duration: 4,
        });
        return false;
      }

      const isCsv =
        file.type === "text/csv" || file.name.toLowerCase().endsWith(".csv");
      if (!isCsv) {
        message.error({
          content: "You can only upload CSV files!",
          duration: 4,
        });
        return false;
      }

      // Read the file content to validate structure
      const fileText = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as string);
        reader.onerror = () => reject("Error reading file");
        reader.readAsText(file);
      });

      const lines = fileText
        .split(/\r\n|\n/)
        .filter((line) => line.trim() !== "");

      // Basic CSV format check (at least 1 header + 1 data row)
      if (lines.length < 2) {
        message.error({
          content: "CSV must have a header and at least one data row.",
          duration: 4,
        });
        return false;
      }

      setFileList([file]);
      setFile(file);
      setNext(true);

      return false; // prevent default upload
    },
    onRemove: () => {
      setUploadProgress(0);
      setUploading(false);
      setFileList([]);
      setFile(null);
      setNext(false);
    },
  };

  const renderStepContent = () => {
    switch (currentStep) {
      case 0:
        return (
          <StepCard title="Instructions" extra={<FileTextOutlined />}>
            <Paragraph>Welcome to the ONDC Polygon Service</Paragraph>
            <InstructionList>
              <InstructionListItem title="Step 1: Generate Token">
                Submit a token generation request by filling out the form. Once
                your request is approved, a token will be issued to you. You can
                access the form by clicking the "Generate Token" button at the
                top of the page.
                <br />
                Use the token sent to your email to log in. This token acts as
                your access credential for the tool.
              </InstructionListItem>

              <InstructionListItem title="Step 2:Download Sample CSV">
                Download the sample CSV template provided. This template will
                guide you in formatting your data correctly before uploading.
                <br />
              </InstructionListItem>

              <InstructionListItem title="Step 3: Upload CSV File">
                Fill in your data following the structure and field requirements
                shown in the sample CSV. Ensure the format matches exactly to
                avoid processing errors.
                <br />
                Go to Step 3 in the tool interface and upload your completed CSV
                file. Once uploaded, click the "Next" button to begin
              </InstructionListItem>

              <InstructionListItem title="Step 4: Download Processed File">
                After the file is processed in Step 4, a final CSV file will be
                generated. You can then download this file for your records or
                further use.
                <br />
                Use the provided GeoJSON data to update the serviceable regions
                in your store catalog accordingly.
              </InstructionListItem>
            </InstructionList>
            {!isAuthenticated && (
              <div style={{ textAlign: "center", marginTop: "24px" }}>
                <Button
                  type="primary"
                  size="large"
                  onClick={onSignInClick}
                  style={{
                    background:
                      "linear-gradient(90deg, #1c75bc, #4aa1e0 51%, #1c75bc) var(--x, 100%) / 200%",
                    color: "#fff",
                  }}
                >
                  Sign In to Continue
                </Button>
              </div>
            )}
          </StepCard>
        );

      case 1:
        return (
          <StepCard title="Download CSV Template" extra={<DownloadOutlined />}>
            <>
              <Paragraph>
                Download the CSV template to understand the required data
                format. This template includes sample data and column headers.
              </Paragraph>
              <Paragraph>
                <ul className="list-disc pl-6 space-y-2 text-gray-700 text-sm">
                  <li>
                    <strong>SNP ID:</strong> Unique ID of the BPP in the message
                    context.
                  </li>
                  <li>
                    <strong>Provider ID:</strong> Unique identifier for a
                    provider.
                  </li>
                  <li>
                    <strong>Location ID:</strong> ID for a specific provider
                    location.
                  </li>
                  <li>
                    <strong>Location GPS:</strong> GPS coordinates of the
                    provider's location in Latitude and longitude .
                    <code className="bg-gray-100 px-1 rounded">"lat,long"</code>{" "}
                    format (e.g.,{" "}
                    <code className="bg-gray-100 px-1 rounded">
                      "28.6139,77.2090"
                    </code>
                    ).
                  </li>

                  <li>
                    <strong>Drive Distance:</strong> Value in meters – Drive
                    distance for catchment.
                  </li>
                  <li>
                    <strong>Drive Time:</strong> Value in minutes – Drive time
                    for catchment.
                  </li>
                </ul>
              </Paragraph>
              <div style={{ textAlign: "center", marginTop: "24px" }}>
                <Button
                  type="primary"
                  size="large"
                  icon={<DownloadOutlined />}
                  onClick={handleDownloadCSV}
                  style={{
                    background:
                      "linear-gradient(90deg, #1c75bc, #4aa1e0 51%, #1c75bc) var(--x, 100%) / 200%",
                    color: "#fff",
                  }}
                >
                  Download Sample CSV
                </Button>
              </div>
            </>
          </StepCard>
        );

      case 2:
        return (
          <StepCard title="Upload Your CSV File" extra={<UploadOutlined />}>
            {isAuthenticated ? (
              <>
                <Paragraph>
                  Upload your prepared CSV file. Make sure it follows the format
                  from the downloaded template.
                </Paragraph>
                <UploadSection>
                  <Upload.Dragger
                    {...uploadProps}
                    maxCount={1}
                    multiple={false}
                    showUploadList={{ showRemoveIcon: true }}
                  >
                    <p className="ant-upload-drag-icon">
                      <UploadOutlined
                        style={{ fontSize: "48px", color: "#1890ff" }}
                      />
                    </p>
                    <p className="ant-upload-text">
                      Click or drag CSV file to this area to upload
                    </p>
                    <p className="ant-upload-hint">
                      Only CSV files are supported. Maximum file size: 10MB
                    </p>
                  </Upload.Dragger>
                </UploadSection>
                {uploading && (
                  <div style={{ marginTop: "24px" }}>
                    <Progress
                      percent={uploadProgress}
                      status={uploadProgress === 100 ? "success" : "active"}
                    />
                  </div>
                )}
                {next && (
                  <div className="flex items-baseline justify-end mt-2">
                    <Button
                      type="primary"
                      onClick={onNextClick}
                      style={{
                        background:
                          "linear-gradient(90deg, #1c75bc, #4aa1e0 51%, #1c75bc) var(--x, 100%) / 200%",
                        color: "#fff",
                      }}
                    >
                      Next
                    </Button>
                  </div>
                )}
              </>
            ) : (
              <div style={{ textAlign: "center", padding: "40px" }}>
                <Title level={4}>Authentication Required</Title>
                <Paragraph>Please sign in to upload CSV files.</Paragraph>
                <Button
                  type="primary"
                  onClick={onSignInClick}
                  style={{
                    background:
                      "linear-gradient(90deg, #1c75bc, #4aa1e0 51%, #1c75bc) var(--x, 100%) / 200%",
                    color: "#fff",
                  }}
                >
                  Sign In
                </Button>
              </div>
            )}
          </StepCard>
        );
      case 3:
        return (
          <StepCard title="Download CSV" extra={<UploadOutlined />}>
            {
              <>
                {id ? (
                  <div className="flex items-baseline justify-between">
                    <Paragraph>
                      {result === "pending" ? (
                        <>
                          Generating CSV file
                          <Spin size="small" style={{ margin: "10px" }} />
                        </>
                      ) : result === "failed" ? (
                        "Processing failed due to errors in the uploaded CSV file. Please download the updated CSV to review the errors, correct them, and try uploading again. If the issue persists, reach out to support for further assistance."
                      ) : result === "fail" ? (
                        "CSV file generation failed. Please try again later or reach out to support if the problem continues."
                      ) : (
                        "You can now download your CSV file by clicking the Download button."
                      )}
                    </Paragraph>
                    {result === "done" && (
                      <Button
                        type="primary"
                        onClick={onDownloadSCV}
                        style={{
                          background:
                            "linear-gradient(90deg, #1c75bc, #4aa1e0 51%, #1c75bc) var(--x, 100%) / 200%",
                          color: "#fff",
                        }}
                      >
                        Download file
                      </Button>
                    )}
                  </div>
                ) : (
                  "To generate and download the processed CSV file, please go back to Step 3, upload your CSV file, and then return to Step 4."
                )}
              </>
            }
          </StepCard>
        );

      default:
        return null;
    }
  };

  return renderStepContent();
};

export default StepContent;
