import React, { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Download, Upload, Clock, ArrowLeft, Key } from "lucide-react";
import { FaFileCsv } from "react-icons/fa";
import { Link } from "react-router-dom";
import axios from "axios";
import { useAuth } from "../contexts/AuthContext";
import { MainContent } from "../components/StyledComponents";
import { message } from "antd";

interface FileStats {
  last_download: {
    filename: string;
    downloaded_at: string;
    download_count: number;
  };
  download_count: number;
  recent_uploads: {
    filename: string;
    created_at: string;
    status: string;
    id: string;
  }[];
  uploads_last_7days: number;
}

const Dashboard: React.FC = () => {
  const [fileStats, setFileStats] = useState<FileStats | null>(null);
  const [tokenStats, setTokenStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [pageNumber, setPageNumber] = useState(1);
  const [perPage, setPerPage] = useState(10);
  const [error, setError] = useState<string | null>(null);
  const { token, logout } = useAuth();
  const apiUrl = import.meta.env.VITE_API_BASE_URL;

  useEffect(() => {
    if (!token) return;
    const fetchFileStats = async () => {
      try {
        const response = await axios.get(
          `${apiUrl}/user-dashboard/stats?page=${pageNumber}&per_page=${perPage}`,
          {
            headers: {
              Authorization: `Bearer ${token}`,
            },
          }
        );

        const res = await axios.get(`${apiUrl}/auth/token-status`, {
          headers: { Authorization: `Bearer ${token}` },
        });

        setTokenStats(res?.data);
        setFileStats(response.data.file_stats);
      } catch (err) {
        setError("Failed to fetch dashboard data.");
      } finally {
        setLoading(false);
      }
    };
    fetchFileStats();
  }, [apiUrl, token, pageNumber, perPage]);

  const handleDownload = async (id: string) => {
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

      const fileName = `catchment_${id}.csv`;
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", fileName);
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

  if (loading) {
    return (
      <div className="flex justify-center items-center h-screen">
        <Skeleton className="w-1/2 h-1/2" />
      </div>
    );
  }

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }

  return (
    <MainContent>
      <div className="flex mb-6">
        <Link to="/" className="text-primary hover:underline">
          <ArrowLeft className="w-5 h-5 text-primary cursor-pointer mr-4" />
        </Link>
        Go back to Home
      </div>

      <div className="bg-white p-10 rounded-xl shadow-lg">
        <h2 className="text-2xl font text-primary mb-4">Dashboard</h2>
        {tokenStats && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center justify-between">
                  Tokens Used
                  <Key className="w-5 h-5 text-primary" />
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font">{tokenStats.tokens.used}</p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center justify-between">
                  Tokens Remaining
                  <Key className="w-5 h-5 text-green-500" />
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font">{tokenStats.tokens.remaining}</p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center justify-between">
                  Token Limit
                  <Key className="w-5 h-5 text-gray-500" />
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font">{tokenStats.tokens.limit}</p>
              </CardContent>
            </Card>
          </div>
        )}
        {fileStats ? (
          <>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center justify-between">
                    Total Downloads
                    <Download className="w-5 h-5 text-primary" />
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-2xl font">{fileStats?.download_count}</p>
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center justify-between">
                    Uploads (Last 7 Days)
                    <Upload className="w-5 h-5 text-primary" />
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-2xl font">
                    {fileStats?.uploads_last_7days}
                  </p>
                </CardContent>
              </Card>
              <Card className="col-span-1 md:col-span-2">
                <CardHeader>
                  <CardTitle className="flex items-center justify-between">
                    Last Download
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {fileStats?.last_download ? (
                    <>
                      <p className="text-lg">
                        {fileStats.last_download.filename}
                      </p>
                      <div className="flex items-center text-sm text-gray-500 mt-2">
                        <Clock className="w-4 h-4 mr-2" />
                        {new Date(
                          fileStats.last_download.downloaded_at
                        ).toLocaleString()}
                      </div>
                    </>
                  ) : (
                    <p className="text-gray-500">No downloads yet</p>
                  )}
                </CardContent>
              </Card>
            </div>

            <div className="mt-6">
              <Card>
                <CardHeader>
                  <CardTitle>Recent Uploads</CardTitle>
                </CardHeader>
                <CardContent>
                  {fileStats?.recent_uploads?.length > 0 ? (
                    <>
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Filename</TableHead>
                            <TableHead>Uploaded At</TableHead>
                            <TableHead>Actions</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {fileStats?.recent_uploads?.map((upload) => (
                            <TableRow key={upload.created_at}>
                              <TableCell>
                                <div className="flex items-center">
                                  <FaFileCsv
                                    className="w-5 h-5 mr-2"
                                    style={{ color: "#00a86b" }}
                                  />
                                  {upload.filename}
                                </div>
                              </TableCell>
                              <TableCell>
                                {new Date(upload.created_at).toLocaleString()}
                              </TableCell>
                              <TableCell>
                                <Button
                                  variant="outline"
                                  size="icon"
                                  onClick={() => handleDownload(upload.id)}
                                >
                                  <Download className="w-5 h-5" />
                                </Button>
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>

                      <div className="flex justify-between items-center mt-4">
                        <Button
                          variant="outline"
                          disabled={pageNumber === 1}
                          onClick={() =>
                            setPageNumber((prev) => Math.max(prev - 1, 1))
                          }
                        >
                          Previous
                        </Button>

                        <span className="text-gray-600">
                          Page {pageNumber} of{" "}
                          {Math.ceil(
                            fileStats?.recent_uploads.length / perPage
                          ) || 1}
                        </span>

                        <Button
                          variant="outline"
                          disabled={
                            pageNumber ===
                            Math.ceil(
                              fileStats?.recent_uploads.length / perPage
                            )
                          }
                          onClick={() =>
                            setPageNumber((prev) =>
                              prev <
                              Math.ceil(
                                fileStats?.recent_uploads.length / perPage
                              )
                                ? prev + 1
                                : prev
                            )
                          }
                        >
                          Next
                        </Button>
                      </div>
                    </>
                  ) : (
                    <div className="text-center text-gray-500 py-4">
                      No recent uploads found
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          </>
        ) : (
          <CardTitle className="flex items-center justify-between">
            No Data Found
          </CardTitle>
        )}
      </div>
    </MainContent>
  );
};

export default Dashboard;
